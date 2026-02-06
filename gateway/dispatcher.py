"""
Dispatcher - 出站消息路由

将 Agent 的回复或主动消息路由到正确的 Channel 或 WebSocket 客户端。
支持两种出站路径：
1. 回复消息：通过 MessageEnvelope.reply_future 直接返回给等待方
2. 主动消息：通过 Channel 或 WebSocket 连接发送（Agent 调用 send_message tool）
"""

import asyncio
import logging
from typing import Callable, Awaitable, Optional, Any

from core.types import OutgoingMessage, MessageEnvelope

logger = logging.getLogger(__name__)

# Channel 发送函数类型: (user_id, OutgoingMessage) -> None
ChannelSendFunc = Callable[[str, OutgoingMessage], Awaitable[None]]


class Dispatcher:
    """
    出站消息路由器
    
    维护两类输出目标的注册表：
    1. channels: channel_name -> send(user_id, OutgoingMessage)
    2. ws_connections: connection_id -> ws send callback
    """
    
    def __init__(self):
        # Channel 发送函数注册表: channel_name -> send_func
        self._channels: dict[str, ChannelSendFunc] = {}
        # WebSocket 连接注册表: connection_id -> send callback
        self._ws_connections: dict[str, Callable] = {}
    
    def register_channel(self, channel_name: str, send_func: ChannelSendFunc):
        """
        注册 Channel 的发送函数
        
        参数:
        - channel_name: Channel 名称 (如 "discord", "telegram")
        - send_func: 异步发送函数 async (user_id, OutgoingMessage) -> None
        """
        self._channels[channel_name] = send_func
        logger.info(f"Dispatcher: registered channel '{channel_name}'")
    
    def unregister_channel(self, channel_name: str):
        """注销 Channel"""
        self._channels.pop(channel_name, None)
        logger.info(f"Dispatcher: unregistered channel '{channel_name}'")
    
    def register_ws(self, connection_id: str, send_func: Callable):
        """
        注册 WebSocket 连接的发送函数
        
        参数:
        - connection_id: 连接唯一标识
        - send_func: 异步发送函数 async (text) -> None
        """
        self._ws_connections[connection_id] = send_func
        logger.debug(f"Dispatcher: registered WebSocket connection '{connection_id}'")
    
    def unregister_ws(self, connection_id: str):
        """注销 WebSocket 连接"""
        self._ws_connections.pop(connection_id, None)
        logger.debug(f"Dispatcher: unregistered WebSocket connection '{connection_id}'")
    
    async def dispatch_reply(self, envelope: MessageEnvelope, response: OutgoingMessage):
        """
        回复一条入站消息
        
        优先使用 reply_future（同步等待方使用），
        否则通过 Channel 发送回复。
        
        参数:
        - envelope: 原始消息信封
        - response: 回复消息
        """
        # 1. 如果有 reply_future，直接 set_result（给 HTTP/WS 等待方）
        if envelope.reply_future and not envelope.reply_future.done():
            envelope.reply_future.set_result(response)
            logger.debug(f"Dispatched reply via future for envelope {envelope.envelope_id}")
            return
        
        # 2. 否则通过 Channel 发送
        msg = envelope.message
        await self.send_to_channel(msg.channel, msg.user_id, response)
    
    async def send_to_channel(self, channel_name: str, user_id: str, message: OutgoingMessage):
        """
        向指定 Channel 发送消息（Agent 主动发送或回复）
        
        参数:
        - channel_name: 目标 Channel 名称
        - user_id: 目标用户 ID
        - message: 要发送的消息
        """
        send_func = self._channels.get(channel_name)
        if not send_func:
            logger.warning(f"Dispatcher: no channel registered for '{channel_name}'")
            return
        
        try:
            await send_func(user_id, message)
            logger.debug(f"Dispatched message to {channel_name}:{user_id}")
        except Exception as e:
            logger.error(f"Dispatcher: failed to send to {channel_name}:{user_id}: {e}", exc_info=True)
    
    async def send_to_ws(self, connection_id: str, text: str):
        """
        向指定 WebSocket 连接发送消息
        
        参数:
        - connection_id: WebSocket 连接 ID
        - text: 要发送的文本
        """
        send_func = self._ws_connections.get(connection_id)
        if not send_func:
            logger.warning(f"Dispatcher: no WebSocket connection '{connection_id}'")
            return
        
        try:
            await send_func(text)
            logger.debug(f"Dispatched message to WebSocket {connection_id}")
        except Exception as e:
            logger.error(f"Dispatcher: failed to send to WebSocket {connection_id}: {e}", exc_info=True)
    
    def list_channels(self) -> list[str]:
        """列出已注册的 Channel"""
        return list(self._channels.keys())
    
    def list_ws_connections(self) -> list[str]:
        """列出已注册的 WebSocket 连接"""
        return list(self._ws_connections.keys())
