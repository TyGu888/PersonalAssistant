"""
Agent 客户端 - Gateway 端使用

提供与直接调用 Agent.run() 相同的接口，
但实际将任务发送到 Worker 进程执行。
"""

import uuid
import logging
from typing import Optional
from dataclasses import dataclass

from worker.protocol import AgentRequest, AgentResponse, PendingPush, SchedulerOp
from worker.pool import WorkerPool
from core.types import ChatMessage

logger = logging.getLogger(__name__)


@dataclass
class AgentRunResult:
    """AgentClient.run() 的返回结果"""
    text: str
    attachments: list[str]
    pending_pushes: list[PendingPush]
    pending_scheduler_ops: list[dict]


class AgentClient:
    """
    Agent 客户端 - Gateway 端使用
    
    提供与直接调用 Agent.run() 相同的接口，
    但实际将任务发送到 Worker 进程执行。
    
    使用示例:
        pool = WorkerPool(config, num_workers=2)
        await pool.start()
        
        client = AgentClient(pool)
        text, attachments, pending_pushes = await client.run(
            agent_id="default",
            user_text="你好",
            context={"history": [...], "memories": [...]},
            tool_names=["web_search", "create_file"]
        )
    """
    
    def __init__(self, pool: WorkerPool):
        """
        初始化 AgentClient
        
        参数:
        - pool: WorkerPool 实例
        """
        self.pool = pool
    
    async def run(
        self,
        agent_id: str,
        user_text: str,
        context: dict,
        tool_names: list[str],
        images: list[str] = None,
        msg_context: dict = None
    ) -> AgentRunResult:
        """
        执行 Agent（在 Worker 进程中）
        
        参数:
        - agent_id: Agent ID（如 "default", "study_coach"）
        - user_text: 用户消息文本
        - context: 上下文 {"history": list[ChatMessage], "memories": list[str]}
        - tool_names: 允许使用的 Tool 名称列表
        - images: 可选的图片列表
        - msg_context: 消息上下文（世界信息）
        
        返回: AgentRunResult
        - text: Agent 响应文本
        - attachments: 需要发送的附件文件路径列表
        - pending_pushes: 需要 Gateway 执行的推送列表
        - pending_scheduler_ops: 需要 Gateway 执行的 scheduler 操作列表
        
        异常:
        - Exception: 如果 Agent 执行失败
        """
        # 序列化上下文
        history = context.get("history", [])
        history_serialized = []
        for msg in history:
            if isinstance(msg, ChatMessage):
                history_serialized.append({
                    "role": msg.role,
                    "content": msg.content
                })
            elif isinstance(msg, dict):
                history_serialized.append(msg)
        
        memories = context.get("memories", [])
        
        # 创建请求
        request = AgentRequest(
            request_id=str(uuid.uuid4()),
            agent_id=agent_id,
            user_text=user_text,
            history=history_serialized,
            memories=memories,
            tool_names=tool_names,
            images=images or [],
            tool_context_data={},
            msg_context=msg_context or {}
        )
        
        logger.debug(
            f"Submitting request {request.request_id} to worker pool: "
            f"agent={agent_id}, tools={tool_names}"
        )
        
        # 提交到 Worker 池
        response = await self.pool.submit(request)
        
        # 处理响应
        if not response.success:
            logger.error(f"Agent execution failed: {response.error}")
            raise Exception(f"Agent error: {response.error}")
        
        # 转换 pending_pushes
        pending_pushes = [
            PendingPush.from_dict(p) 
            for p in response.pending_pushes
        ]
        
        logger.debug(
            f"Request {request.request_id} completed: "
            f"text_len={len(response.text)}, "
            f"attachments={len(response.attachments)}, "
            f"pending_pushes={len(pending_pushes)}, "
            f"pending_scheduler_ops={len(response.pending_scheduler_ops)}"
        )
        
        return AgentRunResult(
            text=response.text,
            attachments=response.attachments,
            pending_pushes=pending_pushes,
            pending_scheduler_ops=response.pending_scheduler_ops
        )
