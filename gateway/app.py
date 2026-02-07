"""
Gateway - 系统中心枢纽

替代旧的 Engine，负责：
1. 初始化和持有 MessageBus、Dispatcher、ChannelManager、GatewayServer
2. 初始化和启动 AgentLoop（embedded 模式）
3. 初始化 MCP Servers
4. 初始化 APScheduler
5. 管理整个系统的生命周期（启动、关闭）
"""

import asyncio
import logging
import os
import re
import yaml
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from gateway.bus import MessageBus
from gateway.dispatcher import Dispatcher
from gateway.channel_manager import ChannelManager
from gateway.server import GatewayServer
from agent.loop import AgentLoop
from agent.runtime import AgentRuntime
from tools.registry import registry
from tools.mcp_client import MCPServer

# 导入 tools 以触发装饰器注册
import tools.scheduler
import tools.filesystem
import tools.web
import tools.shell
import tools.image
import tools.subagent
import tools.channel
import tools.discord_actions

logger = logging.getLogger(__name__)


class Gateway:
    """
    系统中心枢纽
    
    架构：
    Gateway (本类)
    ├── MessageBus          - 消息总线
    ├── Dispatcher          - 出站消息路由
    ├── ChannelManager      - Channel 生命周期
    ├── GatewayServer       - FastAPI + WebSocket
    ├── AgentLoop           - Agent 事件循环
    │   ├── AgentRuntime    - Memory + Tools
    │   └── BaseAgent       - LLM 调用
    └── Scheduler           - 定时任务
    """
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        
        # 核心组件
        self.bus = MessageBus()
        self.dispatcher = Dispatcher()
        self.scheduler = AsyncIOScheduler()
        
        # Channel 管理
        self.channel_manager = ChannelManager(
            bus=self.bus,
            dispatcher=self.dispatcher,
            config=self.config
        )
        
        # Gateway Server (FastAPI)
        gateway_config = self.config.get("gateway", self.config.get("channels", {}).get("http", {}))
        self.server = GatewayServer(
            bus=self.bus,
            dispatcher=self.dispatcher,
            host=gateway_config.get("host", "0.0.0.0"),
            port=gateway_config.get("port", 8080),
            api_key=gateway_config.get("api_key"),
            gateway_ref=self
        )
        
        # Agent Runtime + Loop
        llm_config = self._get_llm_config()
        memory_config = self.config.get("memory", {})
        data_dir = self.config.get("data", {}).get("dir", "./data")
        identity_mode = memory_config.get("identity_mode", "single_owner")
        
        self.runtime = AgentRuntime(
            memory_config=memory_config,
            llm_config=llm_config,
            data_dir=data_dir,
            identity_mode=identity_mode
        )
        
        self.agent_loop = AgentLoop(
            bus=self.bus,
            dispatcher=self.dispatcher,
            runtime=self.runtime,
            config=self.config,
            scheduler=self.scheduler,
            channel_manager=self.channel_manager,
        )
        
        # 进程模式 (保留配置，Worker 迁移后使用)
        engine_config = self.config.get("engine", self.config.get("agent", {}))
        self.process_mode = engine_config.get("process_mode", "embedded")
        
        logger.info(f"Gateway initialized (process_mode={self.process_mode})")
    
    def _load_config(self, config_path: str) -> dict:
        """加载配置文件，支持环境变量替换"""
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        def replace_env(match):
            env_var = match.group(1)
            return os.environ.get(env_var, f"${{{env_var}}}")
        
        content = re.sub(r'\$\{(\w+)\}', replace_env, content)
        return yaml.safe_load(content)
    
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
            logger.info(f"Using LLM profile: {active_profile}")
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
    
    async def _init_mcp_servers(self):
        """初始化 MCP Servers"""
        mcp_config = self.config.get("mcp", {})
        if not mcp_config.get("enabled", False):
            return
        
        servers = mcp_config.get("servers", [])
        if not servers:
            return
        
        logger.info(f"Initializing {len(servers)} MCP server(s)...")
        
        for server_cfg in servers:
            try:
                env = {}
                if server_cfg.get("env"):
                    for key, value in server_cfg["env"].items():
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
                logger.error(f"Failed to init MCP Server {server_cfg.get('name', 'unknown')}: {e}")
        
        mcp_tools = registry.list_mcp_tools()
        if mcp_tools:
            logger.info(f"Registered {len(mcp_tools)} MCP tool(s): {mcp_tools}")
    
    async def _ensure_sandbox_image(self):
        """如果 sandbox 启用，确保 Docker 镜像存在（不存在则自动构建）"""
        sandbox_config = self.config.get("sandbox", {})
        if not sandbox_config.get("enabled", False):
            return
        
        image = sandbox_config.get("image", "personalassistant-sandbox:latest")
        
        try:
            import docker
            client = docker.from_env()
            client.images.get(image)
            logger.info(f"Sandbox image found: {image}")
        except docker.errors.ImageNotFound:
            logger.info(f"Sandbox image '{image}' not found, building...")
            try:
                dockerfile_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Dockerfile.sandbox")
                if not os.path.exists(dockerfile_path):
                    logger.warning(f"Dockerfile.sandbox not found, sandbox will not work")
                    return
                build_context = os.path.dirname(dockerfile_path)
                client.images.build(
                    path=build_context,
                    dockerfile="Dockerfile.sandbox",
                    tag=image,
                    rm=True,
                )
                logger.info(f"Sandbox image built: {image}")
            except Exception as e:
                logger.warning(f"Failed to build sandbox image: {e}")
        except docker.errors.DockerException as e:
            logger.warning(f"Docker not available, sandbox disabled: {e}")
        except Exception as e:
            logger.warning(f"Sandbox image check failed: {e}")
    
    async def run(self):
        """
        启动 Gateway 系统
        
        启动顺序:
        0. 确保 Sandbox 镜像
        1. 初始化 MCP Servers
        2. 初始化 Channels
        3. 启动 Scheduler
        4. 启动 AgentLoop
        5. 启动 Channel 监控
        6. 启动 FastAPI Server（阻塞）
        """
        # 0. Sandbox 镜像
        await self._ensure_sandbox_image()
        
        # 1. MCP
        await self._init_mcp_servers()
        
        # 2. Channels
        self.channel_manager.init_channels()
        
        # 3. Scheduler
        self.scheduler.start()
        
        # 4. AgentLoop
        await self.agent_loop.start()
        
        # 5. Channel 监控
        await self.channel_manager.start_all()
        
        # 6. FastAPI Server
        # 如果配置了 gateway 或 http，启动 server
        gateway_enabled = self.config.get("gateway", {}).get("enabled", 
            self.config.get("channels", {}).get("http", {}).get("enabled", False))
        
        if gateway_enabled:
            try:
                await self.server.start()
            except asyncio.CancelledError:
                logger.info("Gateway server cancelled")
        else:
            # 没有 FastAPI server 时，保持运行
            logger.info("Gateway running without HTTP server (channels only)")
            shutdown_event = asyncio.Event()
            try:
                await shutdown_event.wait()
            except asyncio.CancelledError:
                pass
    
    async def shutdown(self):
        """关闭 Gateway"""
        logger.info("Shutting down Gateway...")
        
        # 停止 AgentLoop
        await self.agent_loop.stop()
        
        # 停止 Channels
        await self.channel_manager.stop_all()
        
        # 停止 Scheduler
        try:
            self.scheduler.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down scheduler: {e}")
        
        # 停止 FastAPI Server
        await self.server.stop()
        
        # 关闭 MessageBus
        await self.bus.close()
        
        # 关闭 MCP
        try:
            await registry.shutdown_mcp()
        except Exception as e:
            logger.error(f"Error shutting down MCP: {e}")
        
        logger.info("Gateway shutdown complete")
