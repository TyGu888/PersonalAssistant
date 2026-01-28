import asyncio
import yaml
import os
import re
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.router import Router
from core.types import IncomingMessage, OutgoingMessage
from channels.base import BaseChannel
from channels.cli import CLIChannel
from channels.telegram import TelegramChannel
from agents.base import BaseAgent
from agents.study_coach import StudyCoachAgent, DefaultAgent
from memory.manager import MemoryManager
from tools.registry import registry

# 导入 tools 以触发装饰器注册
import tools.scheduler


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
        """
        self.config = self._load_config(config_path)
        self.router = Router(self.config.get("routing", []))
        
        # 初始化 MemoryManager
        data_dir = self.config.get("data", {}).get("dir", "./data")
        llm_config = self._get_llm_config()
        self.memory = MemoryManager(data_dir, llm_config)
        
        # 初始化 APScheduler
        self.scheduler = AsyncIOScheduler()
        
        # 初始化 channels 和 agents（延迟初始化）
        self.channels: dict[str, BaseChannel] = {}
        self.agents: dict[str, BaseAgent] = {}
    
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
    
    def _init_agents(self):
        """初始化 Agents"""
        agents_config = self.config.get("agents", {})
        llm_config = self._get_llm_config()
        
        # 初始化 study_coach
        study_coach_cfg = agents_config.get("study_coach", {})
        self.agents["study_coach"] = StudyCoachAgent(
            llm_config=llm_config,
            custom_prompt=study_coach_cfg.get("prompt")
        )
        
        # 初始化 default
        default_cfg = agents_config.get("default", {})
        self.agents["default"] = DefaultAgent(
            llm_config=llm_config,
            custom_prompt=default_cfg.get("prompt")
        )
    
    async def run(self):
        """
        启动服务:
        1. 初始化 channels 和 agents
        2. 启动 scheduler
        3. 启动所有 enabled 的 Channels
        4. 阻塞等待
        """
        self._init_channels()
        self._init_agents()
        
        # 启动 scheduler
        self.scheduler.start()
        
        # 启动所有 channels（并行）
        tasks = []
        for name, channel in self.channels.items():
            tasks.append(asyncio.create_task(channel.start()))
        
        if tasks:
            await asyncio.gather(*tasks)
    
    async def send_push(self, channel: str, user_id: str, text: str):
        """
        主动推送消息（供 Scheduler 等 Tool 调用）
        """
        if channel in self.channels:
            await self.channels[channel].send(user_id, OutgoingMessage(text=text))
    
    def get_tool_context(self) -> dict:
        """
        获取 Tool 执行时需要的上下文（依赖注入）
        """
        return {
            "engine": self,
            "scheduler": self.scheduler,
            "memory": self.memory
        }
    
    async def handle(self, msg: IncomingMessage) -> OutgoingMessage:
        """
        处理消息（核心流程）:
        
        1. session_id = msg.get_session_id()
        2. Router.resolve(msg) -> Route
        3. 获取 Agent 实例
        4. MemoryManager.get_context() -> 历史 + 相关记忆
        5. tools = registry.get_schemas(route.tools)
        6. response = Agent.run(msg.text, context, tools, self.get_tool_context())
        7. MemoryManager.save() -> 保存对话
        8. return OutgoingMessage(text=response)
        """
        try:
            # 1. 获取 session_id
            session_id = msg.get_session_id()
            
            # 2. 路由
            route = self.router.resolve(msg)
            
            # 3. 获取 Agent
            agent = self.agents.get(route.agent_id)
            if agent is None:
                agent = self.agents.get("default")
            
            # 4. 获取上下文
            context = await self.memory.get_context(
                session_id=session_id,
                query=msg.text,
                user_id=msg.user_id
            )
            
            # 5. 获取 Tool schemas
            tools = registry.get_schemas(route.tools)
            
            # 6. 运行 Agent
            response = await agent.run(
                user_text=msg.text,
                context=context,
                tools=tools,
                tool_context=self.get_tool_context()
            )
            
            # 7. 保存对话
            self.memory.save_message(session_id, "user", msg.text)
            self.memory.save_message(session_id, "assistant", response)
            
            # 8. 返回响应
            return OutgoingMessage(text=response)
        
        except Exception as e:
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
        # 停止所有 channels
        for channel in self.channels.values():
            await channel.stop()
        
        # 停止 scheduler
        self.scheduler.shutdown()
