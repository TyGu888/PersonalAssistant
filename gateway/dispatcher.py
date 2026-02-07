"""
Dispatcher - 出站消息路由 + 远程工具 RPC

职责：
1. 回复消息：通过 MessageEnvelope.reply_future 直接返回给等待方
2. 主动消息：通过 Channel 或 WebSocket 连接投递
3. 远程工具 RPC：向已注册的 WebSocket 客户端发起工具调用请求，等待结果
"""

import asyncio
import logging
import uuid
from typing import Callable, Awaitable, Optional, Any

from core.types import OutgoingMessage, MessageEnvelope

logger = logging.getLogger(__name__)

# Channel 投递函数类型: (target: dict, OutgoingMessage) -> None
ChannelDeliverFunc = Callable[[dict, OutgoingMessage], Awaitable[None]]


class Dispatcher:
    """
    出站消息路由器 + 远程工具 RPC
    
    维护三类注册表：
    1. channels: channel_name -> deliver(target: dict, OutgoingMessage)
    2. ws_connections: connection_id -> ws send callback
    3. remote_tools: tool_name -> (connection_id, tool_schema)
    """
    
    def __init__(self):
        # Channel 投递函数注册表: channel_name -> deliver_func
        self._channels: dict[str, ChannelDeliverFunc] = {}
        # WebSocket 连接注册表: connection_id -> send callback (json dict)
        self._ws_connections: dict[str, Callable] = {}
        # 远程工具注册表: tool_name -> {"connection_id": str, "schema": dict}
        self._remote_tools: dict[str, dict] = {}
        # RPC 待回复表: call_id -> asyncio.Future
        self._rpc_pending: dict[str, asyncio.Future] = {}
    
    # ===== Channel 注册 =====
    
    def register_channel(self, channel_name: str, deliver_func: ChannelDeliverFunc):
        self._channels[channel_name] = deliver_func
        logger.info(f"Dispatcher: registered channel '{channel_name}'")
    
    def unregister_channel(self, channel_name: str):
        self._channels.pop(channel_name, None)
        logger.info(f"Dispatcher: unregistered channel '{channel_name}'")
    
    # ===== WebSocket 连接注册 =====
    
    def register_ws(self, connection_id: str, send_func: Callable):
        """注册 WebSocket 连接（send_func 接收 dict，内部 json 序列化）"""
        self._ws_connections[connection_id] = send_func
        logger.debug(f"Dispatcher: registered WebSocket connection '{connection_id}'")
    
    def unregister_ws(self, connection_id: str):
        """注销 WebSocket 连接，同时清理其注册的远程工具"""
        self._ws_connections.pop(connection_id, None)
        # 清理该连接注册的所有远程工具
        to_remove = [name for name, info in self._remote_tools.items()
                     if info["connection_id"] == connection_id]
        for name in to_remove:
            self._remote_tools.pop(name, None)
            logger.info(f"Dispatcher: unregistered remote tool '{name}' (connection gone)")
        # 取消该连接相关的所有 pending RPC
        for call_id, future in list(self._rpc_pending.items()):
            if not future.done():
                future.set_exception(ConnectionError(f"WebSocket {connection_id} disconnected"))
        logger.debug(f"Dispatcher: unregistered WebSocket connection '{connection_id}'")
    
    # ===== 远程工具注册 (客户端提供的工具) =====
    
    def register_remote_tools(self, connection_id: str, tools: list[dict]):
        """
        注册客户端提供的远程工具
        
        参数:
        - connection_id: WebSocket 连接 ID
        - tools: 工具列表, 每个 {"name": str, "description": str, "parameters": dict}
        """
        for tool in tools:
            name = tool["name"]
            self._remote_tools[name] = {
                "connection_id": connection_id,
                "schema": tool,
            }
            logger.info(f"Dispatcher: registered remote tool '{name}' from connection {connection_id[:8]}")
    
    def get_remote_tool_schemas(self) -> list[dict]:
        """获取所有远程工具的 schema（供 AgentLoop 合并到可用工具列表）"""
        return [info["schema"] for info in self._remote_tools.values()]
    
    def get_remote_tool_names(self) -> list[str]:
        """获取所有远程工具名称"""
        return list(self._remote_tools.keys())
    
    async def invoke_remote_tool(self, tool_name: str, arguments: dict, timeout: float = 60.0) -> str:
        """
        RPC 调用远程工具
        
        发送 tool_request 到客户端，等待 tool_result。
        
        参数:
        - tool_name: 工具名称
        - arguments: 参数字典
        - timeout: 超时秒数
        
        返回: 工具执行结果字符串
        """
        tool_info = self._remote_tools.get(tool_name)
        if not tool_info:
            return f"Error: remote tool '{tool_name}' not registered"
        
        connection_id = tool_info["connection_id"]
        send_func = self._ws_connections.get(connection_id)
        if not send_func:
            return f"Error: connection for remote tool '{tool_name}' is gone"
        
        call_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._rpc_pending[call_id] = future
        
        try:
            # 发送 RPC 请求到客户端
            await send_func({
                "type": "tool_request",
                "call_id": call_id,
                "tool_name": tool_name,
                "arguments": arguments,
            })
            
            # 等待客户端回复
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            return f"Error: remote tool '{tool_name}' timed out after {timeout}s"
        except Exception as e:
            return f"Error: remote tool '{tool_name}' failed: {e}"
        finally:
            self._rpc_pending.pop(call_id, None)
    
    def resolve_rpc_result(self, call_id: str, result: str, error: Optional[str] = None):
        """
        客户端回复 RPC 结果时调用
        
        由 GatewayServer WebSocket handler 调用。
        """
        future = self._rpc_pending.get(call_id)
        if not future or future.done():
            logger.warning(f"Dispatcher: no pending RPC for call_id {call_id}")
            return
        
        if error:
            future.set_exception(RuntimeError(error))
        else:
            future.set_result(result)
    
    # ===== 消息路由 =====
    
    async def dispatch_reply(self, envelope: MessageEnvelope, response: OutgoingMessage):
        """投递一条入站消息的回复
        
        1. 如果 reply_future 存在，设置结果（HTTP/WS 同步客户端）
        2. 如果消息来源 channel 有注册投递函数，投递到该 channel
        """
        # 1. 设置 future（HTTP/WS 同步客户端）
        if envelope.reply_future and not envelope.reply_future.done():
            envelope.reply_future.set_result(response)
            logger.debug(f"Dispatched reply via future for envelope {envelope.envelope_id}")
        
        # 2. 投递到 channel（Discord/Telegram 等）
        msg = envelope.message
        deliver_func = self._channels.get(msg.channel)
        if deliver_func:
            target = {**msg.raw, "user_id": msg.user_id}
            await deliver_func(target, response)
    
    async def send_to_channel(self, channel_name: str, target: dict, message: OutgoingMessage):
        """向指定 Channel 投递消息"""
        deliver_func = self._channels.get(channel_name)
        if not deliver_func:
            logger.warning(f"Dispatcher: no channel registered for '{channel_name}'")
            return
        
        try:
            await deliver_func(target, message)
            logger.debug(f"Dispatched message to {channel_name}:{target.get('user_id')}")
        except Exception as e:
            logger.error(f"Dispatcher: failed to deliver to {channel_name}:{target.get('user_id')}: {e}", exc_info=True)
    
    # ===== 查询 =====
    
    def list_channels(self) -> list[str]:
        return list(self._channels.keys())
    
    def list_ws_connections(self) -> list[str]:
        return list(self._ws_connections.keys())
    
    def list_remote_tools(self) -> list[str]:
        return list(self._remote_tools.keys())
