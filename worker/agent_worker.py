"""
Agent Worker - 运行在子进程中

职责:
1. 接收来自 Gateway 的 AgentRequest
2. 加载 Agent 并执行 LLM 调用
3. 执行 Tool calls
4. 返回 AgentResponse

注意:
- Worker 进程有独立的 AgentRuntime（含 MemoryManager）和 Agent 实例
- Tool 中的 send_push 会被转换为 pending_pushes 返回给 Gateway
"""

import asyncio
import multiprocessing as mp
from multiprocessing.connection import Connection
import logging
import os
import sys
import re
import yaml
import signal
from typing import Optional

from worker.protocol import (
    AgentRequest, 
    AgentResponse, 
    PendingPush,
    SHUTDOWN_MESSAGE,
    HEALTH_CHECK_MESSAGE,
    HEALTH_OK_RESPONSE
)

logger = logging.getLogger(__name__)


class WorkerSchedulerProxy:
    """
    Worker 端的 Scheduler 代理
    
    由于 APScheduler 运行在 Gateway 进程中，Worker 无法直接访问。
    此代理收集 scheduler 操作，返回给 Gateway 执行。
    
    注意：当前实现仅支持简单的提醒功能。
    复杂的 auto_continue 模式在分离模式下可能无法正常工作。
    """
    
    def __init__(self, pending_scheduler_ops: list):
        self.pending_scheduler_ops = pending_scheduler_ops
        self._jobs = {}  # 本地缓存，仅用于 get_jobs
    
    def add_job(self, func, trigger, run_date=None, id=None, kwargs=None, replace_existing=False):
        """
        模拟 scheduler.add_job
        
        将操作收集到 pending_scheduler_ops 中。
        """
        self.pending_scheduler_ops.append({
            "op": "add",
            "trigger": trigger,
            "run_date": run_date.isoformat() if run_date else None,
            "job_id": id,
            "kwargs": kwargs or {},
            "replace_existing": replace_existing
        })
        
        # 本地缓存
        self._jobs[id] = {
            "id": id,
            "kwargs": kwargs,
            "next_run_time": run_date
        }
    
    def get_jobs(self):
        """返回本地缓存的 jobs（注意：可能不完整）"""
        return [type('Job', (), job)() for job in self._jobs.values()]
    
    def get_job(self, job_id):
        """获取单个 job"""
        if job_id in self._jobs:
            return type('Job', (), self._jobs[job_id])()
        return None
    
    def remove_job(self, job_id):
        """模拟 scheduler.remove_job"""
        self.pending_scheduler_ops.append({
            "op": "remove",
            "job_id": job_id
        })
        self._jobs.pop(job_id, None)


class WorkerToolContext:
    """
    Worker 端的 Tool Context
    
    模拟 Engine 的部分接口，将需要 Gateway 执行的操作
    收集起来返回给 Gateway。
    
    收集的操作类型：
    - pending_pushes: 需要发送的消息
    - pending_scheduler_ops: scheduler 操作（add_job, remove_job 等）
    """
    
    def __init__(self, memory, scheduler_proxy=None, runtime=None):
        self.memory = memory
        self.runtime = runtime
        self.pending_attachments: list[str] = []
        self.pending_pushes: list[PendingPush] = []
        self.pending_scheduler_ops: list[dict] = []
        
        # 创建 scheduler 代理
        self.scheduler_proxy = scheduler_proxy or WorkerSchedulerProxy(self.pending_scheduler_ops)
    
    async def send_push(self, channel: str, user_id: str, text: str):
        """
        模拟 Engine.send_push
        
        不直接发送，而是收集到 pending_pushes 中，
        返回给 Gateway 执行。
        """
        self.pending_pushes.append(PendingPush(
            channel=channel,
            user_id=user_id,
            text=text
        ))
    
    def to_dict(self) -> dict:
        """转换为 Tool context 字典格式"""
        # 创建一个 mock engine 对象用于 send_push
        mock_engine = type('MockEngine', (), {
            'send_push': self.send_push
        })()
        
        return {
            "engine": mock_engine,
            "runtime": self.runtime,
            "scheduler": self.scheduler_proxy,
            "memory": self.memory,
            "pending_attachments": self.pending_attachments
        }


class AgentWorker:
    """
    Agent Worker - 运行在子进程中
    
    负责:
    1. 接收来自 Gateway 的 AgentRequest
    2. 加载 Agent 并执行 LLM 调用
    3. 执行 Tool calls
    4. 返回 AgentResponse
    """
    
    def __init__(self, config: dict, conn: Connection, worker_id: int = 0):
        self.config = config
        self.conn = conn  # 与 Gateway 的连接
        self.worker_id = worker_id
        self.agents: dict = {}
        self.runtime = None
        self.running = True
        
        # 设置信号处理
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """处理终止信号"""
        logger.info(f"Worker {self.worker_id} received signal {signum}, shutting down...")
        self.running = False
    
    def run(self):
        """Worker 主循环入口"""
        # 配置日志
        self._setup_logging()
        logger.info(f"Worker {self.worker_id} starting...")
        
        try:
            asyncio.run(self._async_run())
        except Exception as e:
            logger.error(f"Worker {self.worker_id} crashed: {e}", exc_info=True)
            raise
        finally:
            logger.info(f"Worker {self.worker_id} exited")
    
    def _setup_logging(self):
        """配置 Worker 进程的日志"""
        logging.basicConfig(
            level=logging.INFO,
            format=f'%(asctime)s - Worker-{self.worker_id} - %(name)s - %(levelname)s - %(message)s',
            handlers=[logging.StreamHandler(sys.stdout)]
        )
    
    async def _async_run(self):
        """异步主循环"""
        # 初始化组件
        await self._init_components()
        
        logger.info(f"Worker {self.worker_id} initialized, entering main loop")
        
        while self.running:
            try:
                # 从管道读取请求（非阻塞轮询）
                if self.conn.poll(timeout=0.1):
                    request_data = self.conn.recv()
                    
                    # 处理控制消息
                    if request_data == SHUTDOWN_MESSAGE:
                        logger.info(f"Worker {self.worker_id} received shutdown message")
                        break
                    
                    if request_data == HEALTH_CHECK_MESSAGE:
                        self.conn.send(HEALTH_OK_RESPONSE)
                        continue
                    
                    # 处理 AgentRequest
                    request = AgentRequest.from_json(request_data)
                    response = await self._handle_request(request)
                    self.conn.send(response.to_json())
                    
            except EOFError:
                logger.warning(f"Worker {self.worker_id}: Connection closed by parent")
                break
            except Exception as e:
                logger.error(f"Worker {self.worker_id} error in main loop: {e}", exc_info=True)
                # 继续运行，不要因为单个请求失败而退出
        
        # 清理资源
        await self._cleanup()
    
    async def _init_components(self):
        """初始化 Agent、Registry、Memory 等组件"""
        # 导入必要模块（在子进程中导入，避免序列化问题）
        from agent.runtime import AgentRuntime
        from agent.base import BaseAgent
        from agent.default import DefaultAgent
        from tools.registry import registry
        from skills.loader import load_skills, get_skill_summaries
        
        # 导入 tools 以触发装饰器注册
        import tools.scheduler
        import tools.filesystem
        import tools.web
        import tools.shell
        import tools.image
        
        # 初始化 AgentRuntime（替代直接创建 MemoryManager）
        data_dir = self.config.get("data", {}).get("dir", "./data")
        llm_config = self._get_llm_config()
        memory_config = self.config.get("memory", {})
        identity_mode = memory_config.get("identity_mode", "single_owner")
        
        self.runtime = AgentRuntime(
            memory_config=memory_config,
            llm_config=llm_config,
            data_dir=data_dir,
            identity_mode=identity_mode
        )
        
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
        
        # 保存 registry 引用
        self.registry = registry
        
        logger.info(f"Worker {self.worker_id} initialized with agents: {list(self.agents.keys())}")
    
    def _get_llm_config(self) -> dict:
        """获取 LLM 配置"""
        llm_cfg = self.config.get("llm", self.config.get("openai", {}))
        return {
            "api_key": llm_cfg.get("api_key"),
            "base_url": llm_cfg.get("base_url"),
            "model": llm_cfg.get("model", "gpt-4o"),
            "max_context_tokens": llm_cfg.get("max_context_tokens", 8000),
            "max_response_tokens": llm_cfg.get("max_response_tokens")
        }
    
    async def _handle_request(self, request: AgentRequest) -> AgentResponse:
        """处理单个 AgentRequest"""
        try:
            logger.info(f"Worker {self.worker_id} handling request {request.request_id} for agent {request.agent_id}")
            
            # 获取 Agent
            agent = self.agents.get(request.agent_id) or self.agents.get("default")
            if not agent:
                return AgentResponse(
                    request_id=request.request_id,
                    success=False,
                    error=f"Agent '{request.agent_id}' not found"
                )
            
            # 重建 context
            from core.types import ChatMessage
            history = [
                ChatMessage(role=h["role"], content=h["content"])
                for h in request.history
            ]
            context = {
                "history": history,
                "memories": request.memories
            }
            
            # 获取 Tool schemas
            tools = self.registry.get_schemas(request.tool_names)
            
            # 创建 Worker 端的 tool_context（使用 runtime 的 memory）
            worker_context = WorkerToolContext(
                memory=self.runtime.memory,
                runtime=self.runtime
            )
            tool_context = worker_context.to_dict()
            
            # 运行 Agent（传递 msg_context）
            response_text = await agent.run(
                user_text=request.user_text,
                context=context,
                tools=tools,
                tool_context=tool_context,
                images=request.images if request.images else None,
                msg_context=request.msg_context if request.msg_context else None
            )
            
            # 收集结果
            return AgentResponse(
                request_id=request.request_id,
                success=True,
                text=response_text,
                attachments=worker_context.pending_attachments,
                pending_pushes=[p.to_dict() for p in worker_context.pending_pushes],
                pending_scheduler_ops=worker_context.pending_scheduler_ops
            )
            
        except Exception as e:
            logger.error(f"Worker {self.worker_id} error handling request: {e}", exc_info=True)
            return AgentResponse(
                request_id=request.request_id,
                success=False,
                error=str(e)
            )
    
    async def _cleanup(self):
        """清理资源"""
        logger.info(f"Worker {self.worker_id} cleaning up...")
        # 可以在这里添加资源清理逻辑


def start_worker(config: dict, conn: Connection, worker_id: int = 0):
    """
    Worker 进程入口函数
    
    此函数作为 multiprocessing.Process 的 target，
    在新进程中执行。
    """
    worker = AgentWorker(config, conn, worker_id)
    worker.run()
