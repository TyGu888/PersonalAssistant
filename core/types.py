import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any
from enum import Enum

# ===== Channel 相关 =====

@dataclass
class IncomingMessage:
    """输入消息（所有 Channel 统一格式）"""
    channel: str              # "discord" | "telegram" | "wechat" | "cli"
    user_id: str              # 用户唯一标识
    text: str                 # 消息文本
    is_group: bool = False    # 是否群聊
    group_id: Optional[str] = None  # 群 ID（群聊时必填）
    timestamp: datetime = field(default_factory=datetime.utcnow)
    attachments: list = field(default_factory=list)  # 附件路径（非图片文件）
    images: list[str] = field(default_factory=list)  # 图片路径或 base64 data URL
    raw: dict = field(default_factory=dict)          # 原始数据
    reply_expected: bool = True  # 是否期望回复（群聊未被 @ 时为 False）
    
    def get_session_id(self) -> str:
        """
        生成 Session Key（标准化）
        
        Session ID 格式:
        - 群聊: {channel}:group:{group_id}（按群记录所有消息）
        - 私聊: {channel}:dm:{user_id}（按用户记录）
        
        设计原则:
        - Session ID 必须全局唯一（考虑多 channel）
        - 群聊按群记录，便于获取完整上下文
        - 私聊按用户记录，保持对话连续性
        """
        if self.is_group:
            # 群聊: 按群记录所有消息（去掉 user_id）
            return f"{self.channel}:group:{self.group_id}"
        # 私聊: 按用户记录
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
    person_id: str            # 统一身份标识（原 user_id）
    type: str                 # "preference" | "fact" | "event" | "commitment" | "env"
    content: str
    embedding: list[float]
    source_session: str
    created_at: datetime
    active: bool = True
    scope: str = "personal"   # "global" | "personal"

# ===== Tool 相关 =====

@dataclass
class ToolResult:
    """Tool 执行结果"""
    success: bool
    output: str
    error: Optional[str] = None

# ===== MessageBus 相关 =====

@dataclass
class MessageEnvelope:
    """
    消息信封 - 包装 IncomingMessage + 回复机制
    
    用于 MessageBus 内部传递。每个入站消息包装成 Envelope，
    附带一个 asyncio.Future 以便发送方等待回复。
    """
    message: IncomingMessage
    reply_future: Optional[asyncio.Future] = None  # 调用方可 await 获取 OutgoingMessage
    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.utcnow)
