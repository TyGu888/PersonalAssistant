import asyncio
import logging
import os
import re
import yaml
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

from core.router import Router
from core.types import IncomingMessage, OutgoingMessage
from channels.base import BaseChannel
from channels.cli import CLIChannel
from channels.telegram import TelegramChannel
from channels.discord import DiscordChannel
from channels.http import HTTPChannel
from agents.base import BaseAgent
from agents.study_coach import StudyCoachAgent, DefaultAgent
from memory.manager import MemoryManager
from tools.registry import registry
from tools.mcp_client import MCPServer
from skills.loader import load_skills, Skill

# 导入 tools 以触发装饰器注册
import tools.scheduler
import tools.filesystem
import tools.web
import tools.shell
import tools.image

# Worker 进程模块（条件导入）
try:
    from worker.pool import WorkerPool
    from worker.agent_client import AgentClient
    from worker.protocol import PendingPush
    WORKER_AVAILABLE = True
except ImportError:
    WORKER_AVAILABLE = False
    WorkerPool = None
    AgentClient = None
    PendingPush = None


class Engine:
    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化:
        1. 加载配置
        2. 初始化 Router
        3. 初始化 MemoryManager
        4. 初始化 APScheduler
        5. 初始化 channels: dict[str, BaseChannel]
        6. 初始化 Agents
        7. 配置进程模式（embedded 或 separated）
        """
        self.config = self._load_config(config_path)
        self.router = Router(self.config.get("routing", []))
        
        # 初始化 MemoryManager
        data_dir = self.config.get("data", {}).get("dir", "./data")
        llm_config = self._get_llm_config()
        memory_config = self.config.get("memory", {})
        self.memory = MemoryManager(data_dir, llm_config, memory_config)
        
        # 初始化 APScheduler
        self.scheduler = AsyncIOScheduler()
        
        # 加载 Skills
        self.skills: dict[str, Skill] = self._load_skills()
        
        # 初始化 channels 和 agents（延迟初始化）
        self.channels: dict[str, BaseChannel] = {}
        self.agents: dict[str, BaseAgent] = {}
        
        # 进程模式配置
        engine_config = self.config.get("engine", {})
        self.process_mode = engine_config.get("process_mode", "embedded")
        self.num_workers = engine_config.get("num_workers", 2)
        
        # Worker 进程池（分离模式时使用）
        self.worker_pool: Optional[WorkerPool] = None
        self.agent_client: Optional[AgentClient] = None
        
        # 验证进程模式配置
        if self.process_mode == "separated" and not WORKER_AVAILABLE:
            logger.warning(
                "process_mode='separated' configured but worker module not available, "
                "falling back to 'embedded' mode"
            )
            self.process_mode = "embedded"
        
        logger.info(f"Engine initialized with process_mode='{self.process_mode}'")
    
    def _load_config(self, config_path: str) -> dict:
        """加载配置文件，支持环境变量替换"""
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 替换 ${ENV_VAR} 格式的环境变量
        def replace_env(match):
            env_var = match.group(1)
            return os.environ.get(env_var, f"${{{env_var}}}")
        
        content = re.sub(r'\$\{(\w+)\}', replace_env, content)
        return yaml.safe_load(content)
    
    def _get_llm_config(self) -> dict:
        """获取 LLM 配置（支持火山引擎等 OpenAI 兼容 API）"""
        # 优先读取新的 llm 配置，兼容旧的 openai 配置
        llm_cfg = self.config.get("llm", self.config.get("openai", {}))
        return {
            "api_key": llm_cfg.get("api_key"),
            "base_url": llm_cfg.get("base_url"),  # 火山引擎等需要自定义 base_url
            "model": llm_cfg.get("model", "gpt-4o")
        }
    
    def _load_skills(self) -> dict[str, Skill]:
        """
        加载 Skills
        
        优先级：
        1. 从 skills/ 目录加载 SKILL.md 文件
        2. 检查 config.yaml 中的 skills.overrides 配置
        """
        skills_config = self.config.get("skills", {})
        skills_dir = skills_config.get("dir", "./skills")
        
        # 加载所有 skills
        skills = load_skills(skills_dir)
        
        # 检查 overrides 配置
        overrides = skills_config.get("overrides", {})
        for skill_name, override_cfg in overrides.items():
            if not override_cfg.get("enabled", True):
                # 禁用此 skill
                if skill_name in skills:
                    logger.info(f"Skill '{skill_name}' 已被禁用")
                    del skills[skill_name]
        
        return skills
    
    def _get_skill_prompt(self, skill_name: str, fallback_prompt: str = None) -> str:
        """
        获取 Skill 的 prompt
        
        优先级：
        1. config.yaml 中 agents.{skill_name}.prompt（如果配置了）
        2. skills/{skill_name}/SKILL.md 中的 prompt
        3. fallback_prompt（默认 prompt）
        
        参数:
        - skill_name: skill 名称
        - fallback_prompt: 后备 prompt
        
        返回: prompt 字符串
        """
        # 1. 检查 config.yaml 中是否有覆盖的 prompt
        agents_config = self.config.get("agents", {})
        agent_cfg = agents_config.get(skill_name, {})
        config_prompt = agent_cfg.get("prompt")
        
        if config_prompt:
            logger.debug(f"使用 config.yaml 中的 prompt: {skill_name}")
            return config_prompt
        
        # 2. 检查是否有对应的 Skill 文件
        if skill_name in self.skills:
            skill = self.skills[skill_name]
            logger.debug(f"使用 Skill 文件中的 prompt: {skill_name}")
            return skill.prompt
        
        # 3. 使用后备 prompt
        logger.debug(f"使用后备 prompt: {skill_name}")
        return fallback_prompt or ""
    
    def _init_channels(self):
        """初始化所有启用的 Channels"""
        channels_config = self.config.get("channels", {})
        
        # CLI Channel
        if channels_config.get("cli", {}).get("enabled", False):
            self.channels["cli"] = CLIChannel(
                on_message=self.handle,
                user_id="cli_user"
            )
        
        # Telegram Channel
        if channels_config.get("telegram", {}).get("enabled", False):
            tg_config = channels_config["telegram"]
            self.channels["telegram"] = TelegramChannel(
                token=tg_config.get("token", ""),
                allowed_users=tg_config.get("allowed_users", []),
                on_message=self.handle
            )
        
        # Discord Channel
        if channels_config.get("discord", {}).get("enabled", False):
            discord_config = channels_config["discord"]
            self.channels["discord"] = DiscordChannel(
                token=discord_config.get("token", ""),
                allowed_users=discord_config.get("allowed_users", []),
                on_message=self.handle
            )
        
        # HTTP API Channel
        if channels_config.get("http", {}).get("enabled", False):
            http_config = channels_config["http"]
            self.channels["http"] = HTTPChannel(
                host=http_config.get("host", "0.0.0.0"),
                port=http_config.get("port", 8080),
                api_key=http_config.get("api_key"),
                on_message=self.handle,
                engine_ref=self  # 传递 Engine 引用以便获取 agents/tools 列表
            )
    
    def _init_agents(self):
        """
        初始化 Agents
        
        优先使用 Skills 中的 prompt，支持向后兼容：
        1. 如果 skills/ 目录中有对应的 SKILL.md，使用其中的 prompt
        2. 否则使用 config.yaml 中的 prompt
        3. 最后回退到 Agent 类的默认 prompt
        """
        llm_config = self._get_llm_config()
        
        # 初始化 study_coach（优先使用 Skill prompt）
        study_coach_prompt = self._get_skill_prompt(
            "study_coach", 
            fallback_prompt=StudyCoachAgent.DEFAULT_PROMPT
        )
        self.agents["study_coach"] = StudyCoachAgent(
            llm_config=llm_config,
            custom_prompt=study_coach_prompt
        )
        
        # 初始化 default（优先使用 Skill prompt）
        default_prompt = self._get_skill_prompt(
            "default",
            fallback_prompt=DefaultAgent.DEFAULT_PROMPT
        )
        self.agents["default"] = DefaultAgent(
            llm_config=llm_config,
            custom_prompt=default_prompt
        )
        
        # 为其他已加载的 Skills 创建通用 Agent（如果尚未初始化）
        for skill_name, skill in self.skills.items():
            if skill_name not in self.agents:
                logger.info(f"为 Skill '{skill_name}' 创建通用 Agent")
                self.agents[skill_name] = BaseAgent(
                    agent_id=skill_name,
                    system_prompt=skill.prompt,
                    llm_config=llm_config
                )
    
    # Channel 监控重连配置
    CHANNEL_INITIAL_RESTART_DELAY = 5    # 初始重启延迟（秒）
    CHANNEL_MAX_RESTART_DELAY = 300      # 最大重启延迟（秒）
    
    async def _init_mcp_servers(self):
        """
        初始化 MCP Servers
        
        从配置文件加载 MCP Server 配置并连接
        """
        mcp_config = self.config.get("mcp", {})
        
        if not mcp_config.get("enabled", False):
            logger.debug("MCP is disabled in config")
            return
        
        servers = mcp_config.get("servers", [])
        if not servers:
            logger.debug("No MCP servers configured")
            return
        
        logger.info(f"Initializing {len(servers)} MCP server(s)...")
        
        for server_cfg in servers:
            try:
                # 处理环境变量
                env = {}
                if server_cfg.get("env"):
                    for key, value in server_cfg["env"].items():
                        # 支持 ${VAR} 格式的环境变量引用
                        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                            env_var = value[2:-1]
                            env[key] = os.environ.get(env_var, "")
                        else:
                            env[key] = value
                
                server = MCPServer(
                    name=server_cfg["name"],
                    command=server_cfg["command"],
                    args=server_cfg.get("args", []),
                    env=env if env else {}
                )
                
                logger.info(f"Connecting to MCP Server: {server.name}")
                await registry.register_mcp_server(server)
                
            except Exception as e:
                logger.error(f"Failed to initialize MCP Server {server_cfg.get('name', 'unknown')}: {e}")
        
        # 打印已注册的 MCP 工具
        mcp_tools = registry.list_mcp_tools()
        if mcp_tools:
            logger.info(f"Registered {len(mcp_tools)} MCP tool(s): {mcp_tools}")
    
    async def run(self):
        """
        启动服务:
        1. 初始化 MCP Servers
        2. 如果是分离模式，启动 Worker 池
        3. 初始化 channels 和 agents
        4. 启动 scheduler
        5. 启动所有 enabled 的 Channels（带监控）
        6. 阻塞等待
        """
        # 先初始化 MCP Servers（需要 await）
        await self._init_mcp_servers()
        
        # 如果是分离模式，启动 Worker 池
        if self.process_mode == "separated":
            logger.info(f"Starting WorkerPool with {self.num_workers} workers...")
            
            # 准备传递给 Worker 的配置（需要可序列化）
            worker_config = self._prepare_worker_config()
            
            self.worker_pool = WorkerPool(worker_config, self.num_workers)
            await self.worker_pool.start()
            self.agent_client = AgentClient(self.worker_pool)
            
            logger.info("WorkerPool started, running in separated mode")
        else:
            logger.info("Running in embedded mode (Agent in same process)")
        
        self._init_channels()
        self._init_agents()
        
        # 初始化监控状态
        self._channel_tasks: dict[str, asyncio.Task] = {}
        self._channel_restart_delays: dict[str, float] = {}
        self._shutdown_event = asyncio.Event()
        
        # 启动 scheduler
        self.scheduler.start()
        
        # 为每个 channel 创建带监控的 task
        for name in self.channels.keys():
            self._channel_restart_delays[name] = self.CHANNEL_INITIAL_RESTART_DELAY
            self._channel_tasks[name] = asyncio.create_task(
                self._monitor_channel(name),
                name=f"channel-monitor-{name}"
            )
        
        if self._channel_tasks:
            # 等待关闭信号或所有 channel 退出
            try:
                await self._shutdown_event.wait()
            except asyncio.CancelledError:
                logger.info("Engine run cancelled")
            finally:
                # 取消所有监控任务
                for task in self._channel_tasks.values():
                    if not task.done():
                        task.cancel()
                
                # 等待所有任务完成
                await asyncio.gather(*self._channel_tasks.values(), return_exceptions=True)
    
    def _prepare_worker_config(self) -> dict:
        """
        准备传递给 Worker 进程的配置
        
        注意: 配置需要可序列化（不能包含对象引用）
        """
        # 深拷贝配置，确保可序列化
        import copy
        worker_config = copy.deepcopy(self.config)
        
        # 移除不需要传递给 Worker 的配置
        # Worker 不需要 channels 配置
        worker_config.pop("channels", None)
        
        # 确保环境变量已替换（在子进程中可能无法访问某些环境变量）
        # LLM API Key
        llm_cfg = worker_config.get("llm", worker_config.get("openai", {}))
        if llm_cfg.get("api_key") and llm_cfg["api_key"].startswith("${"):
            # 环境变量未替换，尝试替换
            env_var = llm_cfg["api_key"][2:-1]
            llm_cfg["api_key"] = os.environ.get(env_var, "")
        
        return worker_config
    
    async def _monitor_channel(self, name: str):
        """
        监控单个 channel，崩溃时自动重启
        
        参数:
        - name: Channel 名称
        """
        channel = self.channels[name]
        
        while not self._shutdown_event.is_set():
            try:
                logger.info(f"Starting channel: {name}")
                await channel.start()
                
                # start() 正常返回意味着 channel 已停止
                # 如果是 shutdown 触发的停止，退出循环
                if self._shutdown_event.is_set():
                    logger.info(f"Channel {name} stopped due to shutdown")
                    break
                
                # 否则是意外退出，尝试重启
                logger.warning(f"Channel {name} exited unexpectedly, will restart")
                
            except asyncio.CancelledError:
                logger.info(f"Channel {name} monitor cancelled")
                break
            except Exception as e:
                logger.error(f"Channel {name} crashed: {e}", exc_info=True)
            
            # 检查是否应该停止
            if self._shutdown_event.is_set():
                break
            
            # 指数退避重启
            delay = self._channel_restart_delays[name]
            logger.warning(f"Restarting channel {name} in {delay}s")
            
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=delay
                )
                # 如果 wait 返回（shutdown_event 被设置），退出循环
                logger.info(f"Channel {name} restart cancelled due to shutdown")
                break
            except asyncio.TimeoutError:
                # 超时意味着该重启了
                pass
            
            # 更新下次重启延迟（指数退避）
            self._channel_restart_delays[name] = min(
                self._channel_restart_delays[name] * 2,
                self.CHANNEL_MAX_RESTART_DELAY
            )
        
        logger.info(f"Channel {name} monitor exited")
    
    def _reset_channel_restart_delay(self, name: str):
        """
        重置 channel 的重启延迟（连接成功后调用）
        
        参数:
        - name: Channel 名称
        """
        if name in self._channel_restart_delays:
            self._channel_restart_delays[name] = self.CHANNEL_INITIAL_RESTART_DELAY
    
    async def send_push(self, channel: str, user_id: str, text: str):
        """
        主动推送消息（供 Scheduler 等 Tool 调用）
        """
        if channel in self.channels:
            await self.channels[channel].send(user_id, OutgoingMessage(text=text))
    
    async def _execute_scheduler_op(self, op: dict, default_channel: str, default_user_id: str):
        """
        执行 Worker 返回的 scheduler 操作
        
        参数:
        - op: scheduler 操作字典
        - default_channel: 默认 channel（从消息上下文获取）
        - default_user_id: 默认 user_id（从消息上下文获取）
        """
        from datetime import datetime
        from dateutil import parser as date_parser
        
        op_type = op.get("op")
        job_id = op.get("job_id")
        
        if op_type == "add":
            # 解析运行时间
            run_date_str = op.get("run_date")
            if run_date_str:
                run_date = date_parser.parse(run_date_str)
            else:
                logger.error(f"Scheduler add op missing run_date: {op}")
                return
            
            kwargs = op.get("kwargs", {})
            replace_existing = op.get("replace_existing", False)
            
            # 获取回调参数
            content = kwargs.get("content", "")
            user_id = kwargs.get("user_id", default_user_id)
            channel = kwargs.get("channel", default_channel)
            auto_continue = kwargs.get("auto_continue", False)
            
            # 创建回调函数
            async def job_callback(content=content, user_id=user_id, channel=channel, auto_continue=auto_continue):
                try:
                    if auto_continue:
                        # 循环提醒：构造系统消息，让 Agent 处理
                        system_msg = IncomingMessage(
                            channel=channel,
                            user_id=user_id,
                            text=f"[定时任务触发] 内容：{content}。请提醒用户，并根据情况决定是否设置下一次提醒（使用 scheduler_add，记得设置 auto_continue=True）。"
                        )
                        response = await self.handle(system_msg)
                        await self.send_push(channel, user_id, response.text)
                    else:
                        # 普通提醒：简单推送
                        await self.send_push(channel, user_id, f"⏰ 提醒: {content}")
                except Exception as e:
                    logger.error(f"Scheduler job callback error: {e}", exc_info=True)
            
            # 添加任务
            self.scheduler.add_job(
                job_callback,
                'date',
                run_date=run_date,
                id=job_id,
                kwargs={
                    'content': content,
                    'user_id': user_id,
                    'channel': channel,
                    'auto_continue': auto_continue
                },
                replace_existing=replace_existing
            )
            logger.info(f"Scheduler job added: {job_id} at {run_date}")
        
        elif op_type == "remove":
            try:
                self.scheduler.remove_job(job_id)
                logger.info(f"Scheduler job removed: {job_id}")
            except Exception as e:
                logger.warning(f"Failed to remove scheduler job {job_id}: {e}")
        
        else:
            logger.warning(f"Unknown scheduler op type: {op_type}")
    
    def get_tool_context(self) -> dict:
        """
        获取 Tool 执行时需要的上下文（依赖注入）
        
        注意: pending_attachments 用于收集 Tool 执行过程中需要发送的文件
        """
        return {
            "engine": self,
            "scheduler": self.scheduler,
            "memory": self.memory,
            "pending_attachments": []  # Tool 可以往这里添加要发送的文件路径
        }
    
    async def handle(self, msg: IncomingMessage) -> OutgoingMessage:
        """
        处理消息（核心流程）:
        
        1. session_id = msg.get_session_id()
        2. 保存用户消息到 memory
        3. 检查 reply_expected，如果为 False 则跳过 Agent 调用
        4. Router.resolve(msg) -> Route
        5. 获取 Agent 实例
        6. MemoryManager.get_context() -> 历史 + 相关记忆
        7. tools = registry.get_schemas(route.tools)
        8. 根据 process_mode 选择执行方式:
           - embedded: 直接调用 Agent.run()
           - separated: 通过 AgentClient 发送到 Worker 进程
        9. MemoryManager.save() -> 保存 assistant 回复
        10. return OutgoingMessage(text=response)
        """
        try:
            # 1. 获取 session_id
            session_id = msg.get_session_id()
            
            # 2. 先保存用户消息（群聊中记录所有消息，不仅是被 @ 的）
            self.memory.save_message(session_id, "user", msg.text)
            
            # 3. 检查是否需要回复（群聊未被 @ 时不回复）
            if not msg.reply_expected:
                return OutgoingMessage(text="")
            
            # 4. 路由
            route = self.router.resolve(msg)
            
            # 5. 获取上下文（从配置读取 max_context_messages）
            max_context_messages = self.config.get("memory", {}).get("max_context_messages", 20)
            context = await self.memory.get_context(
                session_id=session_id,
                query=msg.text,
                user_id=msg.user_id,
                history_limit=max_context_messages
            )
            
            # 6. 根据 process_mode 选择执行方式
            if self.process_mode == "separated" and self.agent_client:
                # 分离模式：通过 AgentClient 发送到 Worker 进程
                result = await self.agent_client.run(
                    agent_id=route.agent_id,
                    user_text=msg.text,
                    context=context,
                    tool_names=route.tools,
                    images=msg.images if msg.images else None
                )
                
                response = result.text
                attachments = result.attachments
                
                # 执行 Worker 返回的 pending_pushes
                for push in result.pending_pushes:
                    try:
                        await self.send_push(push.channel, push.user_id, push.text)
                    except Exception as e:
                        logger.error(f"Failed to execute pending push: {e}")
                
                # 执行 Worker 返回的 pending_scheduler_ops
                for op in result.pending_scheduler_ops:
                    try:
                        await self._execute_scheduler_op(op, msg.channel, msg.user_id)
                    except Exception as e:
                        logger.error(f"Failed to execute scheduler op: {e}")
            else:
                # 内嵌模式：直接调用 Agent
                agent = self.agents.get(route.agent_id)
                if agent is None:
                    agent = self.agents.get("default")
                
                # 获取 Tool schemas
                tools = registry.get_schemas(route.tools)
                
                # 运行 Agent（获取 tool_context 以便收集附件）
                tool_context = self.get_tool_context()
                response = await agent.run(
                    user_text=msg.text,
                    context=context,
                    tools=tools,
                    tool_context=tool_context,
                    images=msg.images if msg.images else None
                )
                
                attachments = tool_context.get("pending_attachments", [])
            
            # 7. 保存 assistant 回复
            self.memory.save_message(session_id, "assistant", response)
            
            # 8. 返回响应（包含 Tool 执行过程中收集的附件）
            return OutgoingMessage(text=response, attachments=attachments)
        
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            return OutgoingMessage(text=f"处理消息时发生错误: {str(e)}")
    
    async def handle_cli(self, text: str, agent_id: str = "default") -> str:
        """CLI 模式处理（单次对话测试用）"""
        msg = IncomingMessage(
            channel="cli",
            user_id="cli_user",
            text=text
        )
        
        # 临时修改路由结果
        response = await self.handle(msg)
        return response.text
    
    async def shutdown(self):
        """关闭 Engine"""
        logger.info("Shutting down Engine...")
        
        # 设置关闭信号，停止所有监控循环
        if hasattr(self, '_shutdown_event'):
            self._shutdown_event.set()
        
        # 停止所有 channels
        for name, channel in self.channels.items():
            try:
                logger.info(f"Stopping channel: {name}")
                await channel.stop()
            except Exception as e:
                logger.error(f"Error stopping channel {name}: {e}", exc_info=True)
        
        # 停止 scheduler
        try:
            self.scheduler.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down scheduler: {e}", exc_info=True)
        
        # 关闭 Worker 池（如果启用了分离模式）
        if self.worker_pool:
            try:
                logger.info("Shutting down WorkerPool...")
                await self.worker_pool.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down WorkerPool: {e}", exc_info=True)
        
        # 关闭所有 MCP 连接
        try:
            logger.info("Shutting down MCP servers...")
            await registry.shutdown_mcp()
        except Exception as e:
            logger.error(f"Error shutting down MCP servers: {e}", exc_info=True)
        
        logger.info("Engine shutdown complete")
    
    def get_worker_status(self) -> Optional[dict]:
        """
        获取 Worker 池状态（供 HTTP API 使用）
        
        返回: Worker 池状态字典，如果未启用分离模式则返回 None
        """
        if self.worker_pool:
            return self.worker_pool.get_status()
        return None
