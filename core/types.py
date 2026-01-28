from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any
from enum import Enum

# ===== Channel 相关 =====

@dataclass
class IncomingMessage:
    """输入消息（所有 Channel 统一格式）"""
    channel: str              # "telegram" | "wechat" | "cli"
    user_id: str              # 用户唯一标识
    text: str                 # 消息文本
    is_group: bool = False    # 是否群聊
    group_id: Optional[str] = None  # 群 ID（群聊时必填）
    timestamp: datetime = field(default_factory=datetime.utcnow)
    attachments: list = field(default_factory=list)  # 附件路径
    raw: dict = field(default_factory=dict)          # 原始数据
    
    def get_session_id(self) -> str:
        """生成 Session Key（标准化）"""
        if self.is_group:
            return f"{self.channel}:group:{self.group_id}:user:{self.user_id}"
        return f"{self.channel}:dm:{self.user_id}"

@dataclass
class OutgoingMessage:
    """输出消息"""
    text: str
    attachments: list = field(default_factory=list)

# ===== Router 相关 =====

@dataclass
class Route:
    """路由结果"""
    agent_id: str             # 目标 Agent ID
    tools: list[str]          # 允许使用的 Tool 列表

# ===== Memory 相关 =====

@dataclass
class ChatMessage:
    """单条对话消息"""
    role: str                 # "user" | "assistant" | "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass
class MemoryItem:
    """一条长期记忆"""
    id: str
    user_id: str
    type: str                 # "preference" | "fact" | "event" | "commitment"
    content: str
    embedding: list[float]
    source_session: str
    created_at: datetime
    active: bool = True

# ===== Tool 相关 =====

@dataclass
class ToolResult:
    """Tool 执行结果"""
    success: bool
    output: str
    error: Optional[str] = None
