from abc import ABC, abstractmethod
from typing import Callable, Awaitable
from core.types import IncomingMessage, OutgoingMessage

# 定义消息处理器类型：接收 IncomingMessage，返回 OutgoingMessage 的异步函数
MessageHandler = Callable[[IncomingMessage], Awaitable[OutgoingMessage]]


class BaseChannel(ABC):
    """
    Channel 基类 - 所有渠道实现需要继承此类
    
    Channel 负责:
    1. 接收用户消息，转换为 IncomingMessage
    2. 调用 on_message 回调处理消息
    3. 将 OutgoingMessage 发送给用户
    4. 支持主动推送消息（如定时提醒）
    """
    
    def __init__(self, on_message: MessageHandler):
        """
        初始化 Channel
        
        参数:
        - on_message: 消息处理回调（由 Engine 传入）
                     当收到用户消息时调用此回调
        """
        self.on_message = on_message
    
    @abstractmethod
    async def start(self):
        """
        启动 Channel
        
        对于 Telegram: 启动 polling
        对于 CLI: 启动输入循环
        """
        pass
    
    @abstractmethod
    async def send(self, user_id: str, message: OutgoingMessage):
        """
        主动发送消息（用于定时提醒等主动推送场景）
        
        参数:
        - user_id: 目标用户 ID
        - message: 要发送的消息
        """
        pass
    
    @abstractmethod
    async def stop(self):
        """
        停止 Channel
        
        清理资源，关闭连接
        """
        pass
