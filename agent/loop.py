"""
AgentLoop - Agent 的事件驱动主循环

Agent 是系统的核心主体。AgentLoop 负责：
1. 从 MessageBus 消费消息（事件驱动）
2. 加载会话上下文（通过 AgentRuntime）
3. 调用 BaseAgent.run() 处理消息
4. 回复通过 Dispatcher 路由回去
5. 支持周期性唤醒（wake_interval）
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime

from gateway.bus import MessageBus
from gateway.dispatcher import Dispatcher
from agent.runtime import AgentRuntime
from agent.base import BaseAgent
from agent.default import DefaultAgent
from core.types import IncomingMessage, OutgoingMessage, MessageEnvelope
from core.router import Router
from skills.loader import load_skills, get_skill_summaries
from tools.registry import registry

logger = logging.getLogger(__name__)


class AgentLoop:
    """
    Agent 事件驱动主循环
    
    - 从 MessageBus.inbox 取消息
    - 通过 AgentRuntime 加载上下文、保存消息
    - 调用 BaseAgent.run() 执行 LLM 推理
    - 通过 Dispatcher 发送回复
    - 支持 wake_interval 周期性唤醒
    """
    
    def __init__(
        self,
        bus: MessageBus,
        dispatcher: Dispatcher,
        runtime: AgentRuntime,
        config: dict,
        scheduler=None,
        channel_manager=None,
    ):
        """
        参数:
        - bus: 消息总线
        - dispatcher: 出站消息路由
        - runtime: Agent 运行时（Memory + Tools）
        - config: 完整配置字典
        - scheduler: APScheduler 实例（可选）
        - channel_manager: ChannelManager 实例（供 channel-specific tools 使用）
        """
        self.bus = bus
        self.dispatcher = dispatcher
        self.runtime = runtime
        self.config = config
        self._scheduler = scheduler
        self._channel_manager = channel_manager
        
        # Agent 配置
        agent_config = config.get("agent", {})
        self.wake_interval = agent_config.get("wake_interval", 0)  # 0 = disabled
        
        # Agents
        self.agents: dict[str, BaseAgent] = {}
        self.router = Router(config.get("routing", []))
        
        # 运行状态
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._active_tasks: set[asyncio.Task] = set()  # 并发处理中的消息
    
    def _init_agents(self):
        """初始化 Agents 和 Skills"""
        llm_config = self._get_llm_config()
        
        # 加载 Skills
        skills_config = self.config.get("skills", {})
        skills_dir = skills_config.get("dir", "./skills")
        skills = load_skills(skills_dir)
        
        # 检查 overrides
        overrides = skills_config.get("overrides", {})
        for skill_name, override_cfg in overrides.items():
            if not override_cfg.get("enabled", True):
                skills.pop(skill_name, None)
        
        self._skills = skills  # 供 run_subagent 按 agent_id 取 skill 的 prompt/tools
        skill_summaries = get_skill_summaries(skills)
        
        # 初始化 DefaultAgent
        self.agents["default"] = DefaultAgent(
            llm_config=llm_config,
            skill_summaries=skill_summaries
        )
        
        logger.info(f"AgentLoop initialized with agents: {list(self.agents.keys())}")
    
    def _get_llm_config(self) -> dict:
        """获取 LLM 配置（支持多 Provider Profile）"""
        llm_cfg = self.config.get("llm", {})
        profiles = self.config.get("llm_profiles", {})
        active_profile = llm_cfg.get("active")
        
        if active_profile and active_profile in profiles:
            profile = profiles[active_profile]
            result = {
                "api_key": profile.get("api_key"),
                "base_url": profile.get("base_url"),
                "model": profile.get("model", "gpt-4o"),
                "extra_params": profile.get("extra_params", {}),
                "features": profile.get("features", {}),
                "profile_name": active_profile
            }
        else:
            result = {
                "api_key": llm_cfg.get("api_key"),
                "base_url": llm_cfg.get("base_url"),
                "model": llm_cfg.get("model", "gpt-4o"),
                "extra_params": llm_cfg.get("extra_params", {}),
                "features": llm_cfg.get("features", {}),
                "profile_name": None
            }
        
        result["max_context_tokens"] = llm_cfg.get("max_context_tokens", 8000)
        result["max_response_tokens"] = llm_cfg.get("max_response_tokens")
        
        # Agent 行为配置（profile 可覆盖 agent 默认值）
        agent_cfg = self.config.get("agent", {})
        result["max_iterations"] = agent_cfg.get("max_iterations", 20)
        profile = profiles.get(active_profile, {}) if active_profile else {}
        result["llm_call_timeout"] = profile.get("llm_call_timeout") or agent_cfg.get("llm_call_timeout", 120)
        result["llm_http_timeout"] = profile.get("llm_http_timeout") or profile.get("timeout") or agent_cfg.get("llm_http_timeout") or result["llm_call_timeout"]
        result["llm_max_retries"] = profile.get("llm_max_retries") or profile.get("max_retries") or agent_cfg.get("llm_max_retries", 2)
        
        return result
    
    async def start(self):
        """启动 AgentLoop"""
        self._init_agents()
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="agent-loop")
        logger.info("AgentLoop started")
    
    async def _run_loop(self):
        """
        主循环 - 并发处理消息
        
        每条消息 spawn 一个 asyncio.Task，多条消息可以同时被 Agent 处理。
        LLM 调用是 I/O-bound，asyncio 天然支持并发。
        """
        logger.info(f"AgentLoop entering main loop (wake_interval={self.wake_interval}s)")
        
        while self._running:
            try:
                # 从 bus 取消息（带超时支持周期性唤醒）
                if self.wake_interval > 0:
                    envelope = await self.bus.consume_timeout(self.wake_interval)
                    if envelope is None:
                        # 周期性唤醒，无消息
                        await self._on_wake()
                        continue
                else:
                    # 纯事件驱动，阻塞等待
                    envelope = await self.bus.consume()
                
                # 并发处理消息（不阻塞主循环）
                task = asyncio.create_task(
                    self._safe_handle_envelope(envelope),
                    name=f"handle-{envelope.envelope_id[:8]}"
                )
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)
                
            except asyncio.CancelledError:
                logger.info("AgentLoop cancelled, waiting for active tasks...")
                # 等待所有活跃任务完成
                if self._active_tasks:
                    await asyncio.gather(*self._active_tasks, return_exceptions=True)
                break
            except Exception as e:
                logger.error(f"AgentLoop error: {e}", exc_info=True)
    
    async def _safe_handle_envelope(self, envelope: MessageEnvelope):
        """安全包装 _handle_envelope，捕获异常避免 task crash"""
        try:
            await self._handle_envelope(envelope)
        except Exception as e:
            logger.error(f"Unhandled error in envelope {envelope.envelope_id}: {e}", exc_info=True)
    
    async def _on_wake(self):
        """
        周期性唤醒 - 发布系统唤醒消息让 Agent 处理
        
        注意：
        - 如果上一次唤醒还在处理中，跳过本次（避免并发唤醒堆积）
        - 唤醒 prompt 明确约束 Agent 行为，避免自作主张
        """
        # 如果有任何活跃的 wake task 还在跑，跳过本次
        for t in self._active_tasks:
            if t.get_name().startswith("wake-"):
                logger.debug("Skipping periodic wake: previous wake task still running")
                return
        
        wake_msg = IncomingMessage(
            channel="system",
            user_id="system",
            text=(
                "[Periodic Wake] You are waking up for a routine check.\n"
                "Based on your role and responsibilities (defined in system prompt), decide what to do:\n"
                "- Check if any of your duties require action right now (e.g. monitoring, data checks, proactive alerts)\n"
                "- Use tools as needed (web_search, send_message, etc.) to fulfill your responsibilities\n"
                "- Use send_message to notify users on the appropriate channel if you find something noteworthy\n"
                "\n"
                "Constraints:\n"
                "- Do NOT re-execute old reminders or past scheduler tasks. They fire independently.\n"
                "- Do NOT repeat actions you already completed in previous wake cycles.\n"
                "- Do NOT add new scheduler_add during wake. Reminders are only added when the user explicitly asks (e.g. \"设个提醒\"). Wake is for checking, not for creating new recurring tasks.\n"
                "- If nothing requires attention right now, respond with <NO_REPLY>."
            ),
            reply_expected=False,
        )
        envelope = MessageEnvelope(message=wake_msg)
        task = asyncio.create_task(
            self._safe_handle_envelope(envelope),
            name=f"wake-{envelope.envelope_id[:8]}"
        )
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
    
    async def _handle_envelope(self, envelope: MessageEnvelope):
        """处理一条消息"""
        msg = envelope.message
        
        try:
            # 1. 获取 session_id
            session_id = msg.get_session_id()
            
            # 2. 保存用户消息（系统唤醒消息不保存，避免污染对话历史）
            if msg.channel != "system":
                self.runtime.save_message(session_id, "user", msg.text)
            
            # 3. 检查是否需要回复
            # 非 system 渠道且不期望回复（如群聊未被 @），跳过 Agent 处理
            # system 唤醒消息虽然 reply_expected=False，但需要 Agent 处理（可能使用 tools）
            if not msg.reply_expected and msg.channel != "system":
                if envelope.reply_future and not envelope.reply_future.done():
                    envelope.reply_future.set_result(OutgoingMessage(text=""))
                return
            
            # 4. 路由（选择 tools）
            route = self.router.resolve(msg)
            
            # 5. 添加 channel_tools
            channel_tools = self._get_channel_tools(msg.channel)
            if channel_tools:
                all_tools = list(route.tools) + [t for t in channel_tools if t not in route.tools]
                route = type(route)(agent_id=route.agent_id, tools=all_tools)
            
            # 6. 解析身份
            person_id = self.runtime.resolve_person_id(msg.channel, msg.user_id)
            
            # 7. 构建消息上下文（世界信息）
            owners = self._get_channel_owners(msg.channel)
            is_owner = msg.user_id in owners
            
            msg_context = {
                "user_id": msg.user_id,
                "person_id": person_id,  # 统一身份 ID，single_owner 时为 "owner"，multi_user 时为 "channel:user_id"
                "channel": msg.channel,
                "timestamp": msg.timestamp,
                "is_group": msg.is_group,
                "group_id": msg.group_id,
                "is_owner": is_owner,
                "session_id": session_id,
                "raw": msg.raw,
                "available_channels": self.dispatcher.list_channels(),
                "contacts": self._channel_manager.get_contacts_summary() if self._channel_manager else {},
                "attachments": msg.attachments if msg.attachments else [],
            }
            # 系统唤醒消息限制最大迭代次数，防止 Agent 自作主张无限调 tool
            if msg.channel == "system":
                msg_context["max_iterations"] = 3
            
            # 8. 加载上下文（历史 + 记忆）
            # 系统唤醒消息：跳过对话历史（避免被旧对话污染），但保留记忆
            # Agent 醒来时靠 system prompt（含 Skill 职责）+ memories 决定行动
            if msg.channel == "system":
                context = await self.runtime.load_context(
                    session_id=session_id,
                    query=msg.text,
                    person_id=person_id,
                    history_limit=0
                )
            else:
                context = await self.runtime.load_context(
                    session_id=session_id,
                    query=msg.text,
                    person_id=person_id
                )
            
            # 9. 获取 Agent
            agent = self.agents.get(route.agent_id) or self.agents.get("default")
            
            # 10. 获取 Tool schemas (本地 + 远程)
            tools = self.runtime.get_tool_schemas(route.tools)
            # 合并远程工具 schemas
            remote_schemas = self.dispatcher.get_remote_tool_schemas()
            if remote_schemas:
                remote_tool_schemas = []
                for schema in remote_schemas:
                    remote_tool_schemas.append({
                        "type": "function",
                        "function": {
                            "name": schema["name"],
                            "description": schema.get("description", ""),
                            "parameters": schema.get("parameters", {"type": "object", "properties": {}})
                        }
                    })
                tools = tools + remote_tool_schemas
            
            # 11. 构建 tool_context
            tool_context = self.runtime.get_tool_context(person_id, session_id, msg_context)
            # 注入 dispatcher 供 channel tools 使用
            tool_context["dispatcher"] = self.dispatcher
            # 注入 scheduler 供定时工具使用
            if self._scheduler:
                tool_context["scheduler"] = self._scheduler
            # 注入 bus 供 auto_continue 定时任务使用
            tool_context["bus"] = self.bus
            # 注入 channel_manager 供 channel-specific tools 使用 (如 tools/discord.py)
            if self._channel_manager:
                tool_context["channel_manager"] = self._channel_manager
            # 注入 agent_loop 供 agent_spawn 等子 Agent 工具使用
            tool_context["agent_loop"] = self

            # 12. 调用 Agent
            response_text = await agent.run(
                user_text=msg.text,
                context=context,
                tools=tools,
                tool_context=tool_context,
                images=msg.images if msg.images else None,
                msg_context=msg_context
            )
            
            attachments = tool_context.get("pending_attachments", [])
            
            # 13. 检查 NO_REPLY
            if response_text and "<NO_REPLY>" in response_text:
                logger.debug(f"Agent returned NO_REPLY for {msg.user_id}")
                response = OutgoingMessage(text="")
            else:
                # 14. 保存 assistant 回复（系统唤醒消息不保存）
                if msg.channel != "system":
                    self.runtime.save_message(session_id, "assistant", response_text)
                response = OutgoingMessage(text=response_text, attachments=attachments)
            
            # 15. 回复
            await self.dispatcher.dispatch_reply(envelope, response)
            
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            error_response = OutgoingMessage(text=f"处理消息时发生错误: {str(e)}")
            await self.dispatcher.dispatch_reply(envelope, error_response)
    
    def _get_channel_tools(self, channel: str) -> list[str]:
        """获取 channel 对应的 tools"""
        channel_tools = self.config.get("channel_tools", {})
        return channel_tools.get(channel, [])
    
    def _get_channel_owners(self, channel: str) -> set[str]:
        """获取 channel 的 owner 列表"""
        channels_config = self.config.get("channels", {})
        channel_config = channels_config.get(channel, {})
        allowed_users = channel_config.get("allowed_users", [])
        return set(str(uid) for uid in allowed_users)

    async def run_subagent(
        self,
        task: str,
        agent_id: str,
        parent_session: str,
        person_id: str,
        timeout_seconds: int,
        run_id: str,
        run_label: str,
    ) -> tuple[bool, str]:
        """
        在当前进程内同步执行子 Agent 任务（不经过 MessageBus）。
        - agent_id 为 skill 名（如 ppt_assistant）时，使用该 skill 的 prompt 与 tools
        - 否则使用 default 路由的 tools，无 system_prompt 覆盖
        返回: (success, result_text 或 error_message)
        """
        child_session = f"subagent:{parent_session}:{run_id}"
        skill = self._skills.get(agent_id) if getattr(self, "_skills", None) else None

        if skill:
            tool_names = list(skill.tools) if skill.tools else []
            system_prompt_override = (
                "[子任务执行]\n你正在作为子 Agent 执行独立任务，完成后直接返回结果。\n\n"
                + skill.prompt
            )
        else:
            dummy_msg = IncomingMessage(channel="subagent", user_id="sys", text="")
            default_route = self.router.resolve(dummy_msg)
            tool_names = list(default_route.tools) if default_route.tools else []
            system_prompt_override = None

        tools = self.runtime.get_tool_schemas(tool_names)
        remote_schemas = self.dispatcher.get_remote_tool_schemas()
        if remote_schemas:
            for s in remote_schemas:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": s["name"],
                        "description": s.get("description", ""),
                        "parameters": s.get("parameters", {"type": "object", "properties": {}}),
                    },
                })

        msg_context = {
            "user_id": f"subagent:{run_id}",
            "person_id": person_id,
            "channel": "subagent",
            "timestamp": datetime.now(),
            "is_group": False,
            "group_id": None,
            "is_owner": True,
            "session_id": child_session,
            "raw": {"parent_session": parent_session, "run_id": run_id, "is_subagent": True},
            "available_channels": self.dispatcher.list_channels(),
            "contacts": self._channel_manager.get_contacts_summary() if self._channel_manager else {},
            "attachments": [],
        }

        context = await self.runtime.load_context(
            session_id=child_session,
            query=task,
            person_id=person_id,
            history_limit=0,
        )
        tool_context = self.runtime.get_tool_context(person_id, child_session, msg_context)
        tool_context["dispatcher"] = self.dispatcher
        if self._scheduler:
            tool_context["scheduler"] = self._scheduler
        tool_context["bus"] = self.bus
        if self._channel_manager:
            tool_context["channel_manager"] = self._channel_manager
        tool_context["agent_loop"] = self

        agent = self.agents.get("default")
        if not agent:
            return False, "错误: 无 default agent"

        self.runtime.save_message(child_session, "user", task)
        try:
            response_text = await asyncio.wait_for(
                agent.run(
                    user_text=task,
                    context=context,
                    tools=tools,
                    tool_context=tool_context,
                    images=None,
                    msg_context=msg_context,
                    system_prompt_override=system_prompt_override,
                ),
                timeout=timeout_seconds,
            )
            self.runtime.save_message(child_session, "assistant", response_text or "")
            return True, response_text or ""
        except asyncio.TimeoutError:
            return False, f"任务超时（{timeout_seconds}秒）"
        except Exception as e:
            logger.exception(f"SubAgent {run_id} 执行异常")
            return False, f"执行失败: {str(e)}"

    async def stop(self):
        """停止 AgentLoop"""
        logger.info("Stopping AgentLoop...")
        self._running = False
        
        # 等待所有活跃的消息处理任务完成
        if self._active_tasks:
            logger.info(f"Waiting for {len(self._active_tasks)} active task(s) to finish...")
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("AgentLoop stopped")
