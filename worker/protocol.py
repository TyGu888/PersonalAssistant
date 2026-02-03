"""
Worker 通信协议定义

定义 Gateway 与 Worker 进程之间的通信消息格式。
所有消息通过 JSON 序列化在 Pipe 中传输。
"""

from dataclasses import dataclass, field
from typing import Optional, Any
import json
from datetime import datetime


@dataclass
class PendingPush:
    """
    待执行的推送消息
    
    Worker 中的 Tool 无法直接访问 Channel，
    需要将推送请求返回给 Gateway 执行。
    """
    channel: str
    user_id: str
    text: str
    
    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "user_id": self.user_id,
            "text": self.text
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PendingPush':
        return cls(
            channel=data["channel"],
            user_id=data["user_id"],
            text=data["text"]
        )


@dataclass
class AgentRequest:
    """
    发送给 Worker 的请求
    
    包含执行 Agent.run() 所需的全部信息（已序列化）。
    """
    request_id: str
    agent_id: str
    user_text: str
    
    # 上下文信息（已序列化）
    history: list[dict]          # [{"role": "user", "content": "..."}, ...]
    memories: list[str]          # 相关长期记忆
    
    # Tool 相关
    tool_names: list[str]        # Tool 名称列表（Worker 端重新获取 schemas）
    
    # 可选参数
    images: list[str] = field(default_factory=list)  # 图片列表
    
    # Tool context 中可序列化的数据
    tool_context_data: dict = field(default_factory=dict)
    
    # 消息上下文（世界信息）
    msg_context: dict = field(default_factory=dict)  # 包含 user_id, channel, timestamp, is_group 等
    
    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        # 序列化 msg_context 中的 datetime
        msg_context_serialized = dict(self.msg_context) if self.msg_context else {}
        if msg_context_serialized.get("timestamp"):
            ts = msg_context_serialized["timestamp"]
            if hasattr(ts, "isoformat"):
                msg_context_serialized["timestamp"] = ts.isoformat()
        
        return json.dumps({
            "request_id": self.request_id,
            "agent_id": self.agent_id,
            "user_text": self.user_text,
            "history": self.history,
            "memories": self.memories,
            "tool_names": self.tool_names,
            "images": self.images,
            "tool_context_data": self.tool_context_data,
            "msg_context": msg_context_serialized
        }, ensure_ascii=False)
    
    @classmethod
    def from_json(cls, data: str) -> 'AgentRequest':
        """从 JSON 字符串反序列化"""
        obj = json.loads(data)
        
        # 反序列化 msg_context 中的 timestamp
        msg_context = obj.get("msg_context", {})
        if msg_context.get("timestamp"):
            from dateutil import parser as date_parser
            try:
                msg_context["timestamp"] = date_parser.parse(msg_context["timestamp"])
            except:
                pass  # 保持原值
        
        return cls(
            request_id=obj["request_id"],
            agent_id=obj["agent_id"],
            user_text=obj["user_text"],
            history=obj["history"],
            memories=obj["memories"],
            tool_names=obj["tool_names"],
            images=obj.get("images", []),
            tool_context_data=obj.get("tool_context_data", {}),
            msg_context=msg_context
        )


@dataclass
class SchedulerOp:
    """
    Scheduler 操作
    
    Worker 中的 scheduler_add 等 Tool 会生成这些操作，
    返回给 Gateway 在主进程中执行。
    """
    op: str                        # "add" 或 "remove"
    job_id: str
    
    # add 操作的参数
    trigger: str = None            # "date", "interval", etc.
    run_date: str = None           # ISO 格式的日期时间
    kwargs: dict = field(default_factory=dict)
    replace_existing: bool = False
    
    def to_dict(self) -> dict:
        return {
            "op": self.op,
            "job_id": self.job_id,
            "trigger": self.trigger,
            "run_date": self.run_date,
            "kwargs": self.kwargs,
            "replace_existing": self.replace_existing
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'SchedulerOp':
        return cls(
            op=data["op"],
            job_id=data.get("job_id"),
            trigger=data.get("trigger"),
            run_date=data.get("run_date"),
            kwargs=data.get("kwargs", {}),
            replace_existing=data.get("replace_existing", False)
        )


@dataclass  
class AgentResponse:
    """
    Worker 返回的响应
    
    包含 Agent.run() 的执行结果和需要 Gateway 处理的副作用。
    """
    request_id: str
    success: bool
    
    # 成功时的响应
    text: str = ""
    attachments: list[str] = field(default_factory=list)  # 文件路径列表
    
    # 需要 Gateway 执行的推送
    pending_pushes: list[dict] = field(default_factory=list)  # PendingPush.to_dict()
    
    # 需要 Gateway 执行的 scheduler 操作
    pending_scheduler_ops: list[dict] = field(default_factory=list)  # SchedulerOp.to_dict()
    
    # 失败时的错误信息
    error: str = None
    
    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps({
            "request_id": self.request_id,
            "success": self.success,
            "text": self.text,
            "attachments": self.attachments,
            "pending_pushes": self.pending_pushes,
            "pending_scheduler_ops": self.pending_scheduler_ops,
            "error": self.error
        }, ensure_ascii=False)
    
    @classmethod
    def from_json(cls, data: str) -> 'AgentResponse':
        """从 JSON 字符串反序列化"""
        obj = json.loads(data)
        return cls(
            request_id=obj["request_id"],
            success=obj["success"],
            text=obj.get("text", ""),
            attachments=obj.get("attachments", []),
            pending_pushes=obj.get("pending_pushes", []),
            pending_scheduler_ops=obj.get("pending_scheduler_ops", []),
            error=obj.get("error")
        )


# Worker 控制消息
SHUTDOWN_MESSAGE = "__SHUTDOWN__"
HEALTH_CHECK_MESSAGE = "__HEALTH_CHECK__"
HEALTH_OK_RESPONSE = "__HEALTH_OK__"
