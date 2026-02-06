from abc import ABC, abstractmethod
from typing import Optional
import asyncio
import logging

from core.types import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class ReconnectMixin:
    """
    重连 Mixin - 提供指数退避重连逻辑
    
    提供:
    1. is_running 状态标志
    2. _should_stop 停止标志
    3. 指数退避重连策略（初始 5秒，最大 300秒）
    4. max_retries 配置（默认 None 表示无限重试）
    """
    
    # 重连配置
    INITIAL_RECONNECT_DELAY = 5  # 初始延迟（秒）
    MAX_RECONNECT_DELAY = 300    # 最大延迟（秒）
    
    def __init_reconnect__(self, max_retries: Optional[int] = None):
        """
        初始化重连状态
        
        参数:
        - max_retries: 最大重试次数，None 表示无限重试
        """
        self.is_running: bool = False
        self._should_stop: bool = False
        self._reconnect_delay: float = self.INITIAL_RECONNECT_DELAY
        self._retry_count: int = 0
        self._max_retries: Optional[int] = max_retries
    
    def _reset_reconnect_state(self):
        """重置重连状态（连接成功后调用）"""
        self._reconnect_delay = self.INITIAL_RECONNECT_DELAY
        self._retry_count = 0
    
    def _get_next_delay(self) -> float:
        """
        获取下次重连延迟时间（指数退避）
        
        返回: 延迟秒数
        """
        delay = self._reconnect_delay
        # 指数退避：5s -> 10s -> 20s -> 40s -> ... -> 300s (max)
        self._reconnect_delay = min(
            self._reconnect_delay * 2,
            self.MAX_RECONNECT_DELAY
        )
        self._retry_count += 1
        return delay
    
    def _should_retry(self) -> bool:
        """
        检查是否应该继续重试
        
        返回: True 如果应该重试，False 如果应该停止
        """
        if self._should_stop:
            return False
        if self._max_retries is not None and self._retry_count >= self._max_retries:
            return False
        return True
    
    async def _wait_for_reconnect(self, channel_name: str) -> bool:
        """
        等待重连延迟
        
        参数:
        - channel_name: Channel 名称（用于日志）
        
        返回: True 如果应该继续重试，False 如果被取消或达到最大重试次数
        """
        if not self._should_retry():
            return False
        
        delay = self._get_next_delay()
        logger.warning(
            f"{channel_name} reconnecting in {delay}s "
            f"(attempt {self._retry_count}, max_retries={self._max_retries or 'unlimited'})"
        )
        
        try:
            await asyncio.sleep(delay)
            return not self._should_stop
        except asyncio.CancelledError:
            logger.info(f"{channel_name} reconnect wait cancelled")
            return False


class BaseChannel(ABC):
    """
    Channel 基类 - 消息总线版本
    
    Channel 负责:
    1. 接收外部消息，转换为 IncomingMessage
    2. 通过 MessageBus 发布消息（不再直接调用 on_message）
    3. 将 OutgoingMessage 发送给用户
    4. 支持主动推送消息（如定时提醒）
    """
    
    def __init__(self):
        """初始化 Channel（不再需要 on_message 回调）"""
        self._bus = None  # 由 ChannelManager 注入
    
    def set_bus(self, bus):
        """
        注入 MessageBus（由 ChannelManager 调用）
        
        参数:
        - bus: MessageBus 实例
        """
        from gateway.bus import MessageBus
        self._bus = bus
    
    async def publish_message(self, msg: IncomingMessage) -> Optional[OutgoingMessage]:
        """
        发布消息到 MessageBus
        
        Channel 收到消息后调用此方法。
        对于需要同步回复的 Channel（如 Telegram 直接回复），
        可以 await 获取回复。
        
        参数:
        - msg: 入站消息
        
        返回: OutgoingMessage（如果等待回复）或 None
        """
        if not self._bus:
            logger.error("MessageBus not set, cannot publish message")
            return None
        return await self._bus.publish(msg, wait_reply=True)
    
    @abstractmethod
    async def start(self):
        """启动 Channel"""
        pass
    
    @abstractmethod
    async def send(self, user_id: str, message: OutgoingMessage):
        """主动发送消息"""
        pass
    
    @abstractmethod
    async def stop(self):
        """停止 Channel"""
        pass
