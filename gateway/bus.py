"""
MessageBus - Gateway 与 Agent 之间的异步消息通道

Inbox: Channel/Client → Agent（入站消息）
提供 publish/consume 接口，MessageEnvelope 包装每条消息。
"""

import asyncio
import logging
from typing import Optional
from core.types import IncomingMessage, OutgoingMessage, MessageEnvelope

logger = logging.getLogger(__name__)


class MessageBus:
    """
    异步消息总线
    
    - Channel/FastAPI 调用 publish() 将消息放入 inbox
    - AgentLoop 调用 consume() 从 inbox 取消息
    - publish() 返回 asyncio.Future，调用方可选择 await 等待回复
    - AgentLoop 处理完后调用 envelope.reply_future.set_result() 完成回复
    """
    
    def __init__(self, maxsize: int = 0):
        self.inbox: asyncio.Queue[MessageEnvelope] = asyncio.Queue(maxsize=maxsize)
        self._closed = False
    
    async def publish(self, msg: IncomingMessage, wait_reply: bool = False) -> Optional[OutgoingMessage]:
        """
        发布消息到 inbox
        
        参数:
        - msg: 入站消息
        - wait_reply: 是否等待回复（HTTP/WebSocket 客户端需要等待）
        
        返回:
        - 如果 wait_reply=True，返回 OutgoingMessage
        - 如果 wait_reply=False，返回 None（fire-and-forget）
        """
        if self._closed:
            raise RuntimeError("MessageBus is closed")
        
        loop = asyncio.get_event_loop()
        future = loop.create_future() if wait_reply else None
        
        envelope = MessageEnvelope(
            message=msg,
            reply_future=future
        )
        
        await self.inbox.put(envelope)
        logger.debug(f"Published message {envelope.envelope_id} from {msg.channel}:{msg.user_id}")
        
        if wait_reply and future is not None:
            # 等待 Agent 处理完成
            return await future
        
        return None
    
    async def consume(self) -> MessageEnvelope:
        """
        从 inbox 取一条消息（阻塞等待）
        
        AgentLoop 调用此方法获取下一条待处理的消息。
        """
        return await self.inbox.get()
    
    async def consume_timeout(self, timeout: float) -> Optional[MessageEnvelope]:
        """
        从 inbox 取消息，带超时
        
        用于 AgentLoop 的周期性唤醒：
        - 如果有消息，立即返回
        - 如果超时，返回 None（Agent 可以做周期性任务）
        
        参数:
        - timeout: 超时秒数
        
        返回: MessageEnvelope 或 None（超时）
        """
        try:
            return await asyncio.wait_for(self.inbox.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    def pending_count(self) -> int:
        """返回 inbox 中待处理的消息数量"""
        return self.inbox.qsize()
    
    async def close(self):
        """关闭 MessageBus"""
        self._closed = True
        logger.info("MessageBus closed")
