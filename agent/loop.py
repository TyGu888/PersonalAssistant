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
    ):
        """
        参数:
        - bus: 消息总线
        - dispatcher: 出站消息路由
        - runtime: Agent 运行时（Memory + Tools）
        - config: 完整配置字典
        """
        self.bus = bus
        self.dispatcher = dispatcher
        self.runtime = runtime
        self.config = config
        
        # Agent 配置
        agent_config = config.get("agent", {})
        self.wake_interval = agent_config.get("wake_interval", 0)  # 0 = disabled
        
        # Agents
        self.agents: dict[str, BaseAgent] = {}
        self.router = Router(config.get("routing", []))
        
        # 运行状态
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
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
        return result
    
    async def start(self):
        """启动 AgentLoop"""
        self._init_agents()
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="agent-loop")
        logger.info("AgentLoop started")
    
    async def _run_loop(self):
        """主循环"""
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
                
                # 处理消息
                await self._handle_envelope(envelope)
                
            except asyncio.CancelledError:
                logger.info("AgentLoop cancelled")
                break
            except Exception as e:
                logger.error(f"AgentLoop error: {e}", exc_info=True)
    
    async def _on_wake(self):
        """
        周期性唤醒回调
        
        Agent 定期醒来可以做的事情：
        - 检查待办事项
        - 发送定时提醒
        - 主动问候
        
        当前实现：仅记录日志。后续可扩展。
        """
        logger.debug("AgentLoop periodic wake (no pending messages)")
    
    async def _handle_envelope(self, envelope: MessageEnvelope):
        """处理一条消息"""
        msg = envelope.message
        
        try:
            # 1. 获取 session_id
            session_id = msg.get_session_id()
            
            # 2. 保存用户消息
            self.runtime.save_message(session_id, "user", msg.text)
            
            # 3. 检查是否需要回复
            if not msg.reply_expected:
                # 群聊未被 @ ，不需要回复
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
                "channel": msg.channel,
                "timestamp": msg.timestamp,
                "is_group": msg.is_group,
                "group_id": msg.group_id,
                "is_owner": is_owner,
                "session_id": session_id,
                "raw": msg.raw
            }
            
            # 8. 加载上下文（历史 + 记忆）
            context = await self.runtime.load_context(
                session_id=session_id,
                query=msg.text,
                person_id=person_id
            )
            
            # 9. 获取 Agent
            agent = self.agents.get(route.agent_id) or self.agents.get("default")
            
            # 10. 获取 Tool schemas
            tools = self.runtime.get_tool_schemas(route.tools)
            
            # 11. 构建 tool_context
            tool_context = self.runtime.get_tool_context(person_id, session_id, msg_context)
            # 注入 dispatcher 供 channel tools 使用
            tool_context["dispatcher"] = self.dispatcher
            
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
                # 14. 保存 assistant 回复
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
    
    async def stop(self):
        """停止 AgentLoop"""
        logger.info("Stopping AgentLoop...")
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("AgentLoop stopped")
