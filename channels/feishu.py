"""
Feishu (Lark) Channel - 飞书 Bot

使用 lark-oapi SDK + WebSocket 模式连接
无需公网 HTTP endpoint

配置:
- app_id: 飞书应用 App ID
- app_secret: 飞书应用 App Secret
- encrypt_key: 事件加密 Key (可选, 空字符串表示不加密)
- verification_token: 验证 Token (可选, 空字符串表示不验证)
- allowed_users: 白名单用户 open_id 列表
"""

import asyncio
import json
import logging
import threading
from typing import Optional

from channels.base import BaseChannel, ReconnectMixin
from core.types import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class FeishuChannel(BaseChannel, ReconnectMixin):
    
    def __init__(self, app_id: str, app_secret: str, 
                 encrypt_key: str = "", verification_token: str = "",
                 allowed_users: list = None):
        BaseChannel.__init__(self)
        self.__init_reconnect__()
        self.app_id = app_id
        self.app_secret = app_secret
        self.encrypt_key = encrypt_key
        self.verification_token = verification_token
        self.allowed_users = set(str(u) for u in (allowed_users or []))
        self.lark_client = None  # lark.Client for API calls
        self.ws_client = None  # lark.ws.Client for WebSocket
        self._ws_thread = None
        self._loop = None  # reference to the main asyncio event loop
    
    async def start(self):
        """Start Feishu Bot with reconnect loop"""
        self.is_running = True
        self._should_stop = False
        self._loop = asyncio.get_event_loop()
        
        while not self._should_stop:
            try:
                await self._connect()
                
                # Wait for the WS thread to finish (it shouldn't unless error)
                while not self._should_stop and self._ws_thread and self._ws_thread.is_alive():
                    await asyncio.sleep(1)
                
                if self._should_stop:
                    break
                logger.warning("Feishu Bot WebSocket disconnected")
                
            except asyncio.CancelledError:
                logger.info("Feishu Bot start cancelled")
                break
            except Exception as e:
                logger.error(f"Feishu Bot connection error: {e}", exc_info=True)
                await self._cleanup()
                if not await self._wait_for_reconnect("FeishuChannel"):
                    break
        
        self.is_running = False
        logger.info("Feishu Bot exited")
    
    async def _connect(self):
        """Setup lark client and start WebSocket in background thread"""
        try:
            import lark_oapi as lark
        except ImportError:
            raise ImportError("lark-oapi not installed. Run: pip install lark-oapi")
        
        # Create API client
        self.lark_client = lark.Client.builder() \
            .app_id(self.app_id) \
            .app_secret(self.app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()
        
        # Create event handler
        event_handler = lark.EventDispatcherHandler.builder(
            self.encrypt_key, self.verification_token
        ).register_p2_im_message_receive_v1(
            self._on_message_sync  # sync callback, called from WS thread
        ).build()
        
        # Create WebSocket client
        self.ws_client = lark.ws.Client(
            self.app_id, self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO
        )
        
        self._reset_reconnect_state()
        logger.info("Feishu Bot connecting via WebSocket...")
        
        # Run blocking ws_client.start() in a background thread
        self._ws_thread = threading.Thread(
            target=self._run_ws_blocking,
            name="feishu-ws",
            daemon=True
        )
        self._ws_thread.start()
        
        # Wait a moment for connection to establish
        await asyncio.sleep(2)
        
        # Startup scan
        await self._startup_scan()
    
    def _run_ws_blocking(self):
        """Run the blocking WebSocket client in a thread"""
        try:
            self.ws_client.start()
        except Exception as e:
            logger.error(f"Feishu WebSocket error: {e}", exc_info=True)
    
    def _on_message_sync(self, data) -> None:
        """
        Sync callback from WebSocket thread.
        Schedule async processing on the main event loop.
        """
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._on_message(data), self._loop
            )
    
    async def _on_message(self, data) -> None:
        """Process incoming Feishu message"""
        try:
            event = data.event
            msg = event.message
            sender = event.sender
            
            # Extract sender info
            user_id = sender.sender_id.open_id if sender.sender_id else ""
            if not user_id:
                return
            
            # Check whitelist
            if self.allowed_users and str(user_id) not in self.allowed_users:
                logger.debug(f"Feishu: unauthorized user {user_id}")
                return
            
            # Parse message content (JSON string)
            text = ""
            try:
                content = json.loads(msg.content)
                text = content.get("text", "")
            except (json.JSONDecodeError, TypeError):
                text = msg.content or ""
            
            if not text:
                return
            
            # Remove @mention text if present
            # Feishu includes @_user_1 pattern for mentions
            import re
            text = re.sub(r'@_user_\d+\s*', '', text).strip()
            
            chat_id = msg.chat_id
            chat_type = msg.chat_type  # "p2p" or "group"
            is_group = chat_type == "group"
            
            # In groups, check if bot is mentioned
            mentions = getattr(msg, 'mentions', None) or []
            is_mentioned = False
            for mention in mentions:
                if hasattr(mention, 'id') and hasattr(mention.id, 'open_id'):
                    # Check if it's the bot
                    pass  # Feishu mentions include all @'d users, hard to filter bot
                is_mentioned = True  # If any mentions exist, treat as mentioned for simplicity
            
            reply_expected = not is_group or is_mentioned
            
            raw = {
                "chat_id": chat_id,
                "message_id": msg.message_id,
                "chat_type": chat_type,
                "user_id": user_id,
                "sender_type": sender.sender_type,
            }
            
            incoming = IncomingMessage(
                channel="feishu",
                user_id=str(user_id),
                text=text,
                is_group=is_group,
                group_id=chat_id if is_group else None,
                reply_expected=reply_expected,
                raw=raw,
            )
            
            logger.info(f"Feishu message from {user_id}: {text[:50]}...")
            await self.publish_message(incoming)
            
        except Exception as e:
            logger.error(f"Error handling Feishu message: {e}", exc_info=True)
    
    async def _startup_scan(self):
        """Scan bot's joined chats"""
        if not self.lark_client or not self._contact_callback:
            return
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import ListChatRequest
            
            scan_info = {"status": "connected", "chats": {}}
            
            request = ListChatRequest.builder().page_size(50).build()
            response = await self.lark_client.im.v1.chat.alist(request)
            
            if response.success() and response.data and response.data.items:
                for chat in response.data.items:
                    scan_info["chats"][chat.chat_id] = {
                        "name": chat.name or chat.chat_id,
                        "type": chat.chat_type or "unknown"
                    }
            
            self._contact_callback(scan_info)
            logger.info(f"Feishu startup scan: {len(scan_info['chats'])} chat(s)")
        except Exception as e:
            logger.warning(f"Feishu startup scan failed: {e}")
    
    def extract_contact_info(self, msg: IncomingMessage) -> dict:
        """Extract contact info from Feishu message"""
        raw = msg.raw or {}
        info = {}
        chat_id = raw.get("chat_id")
        chat_type = raw.get("chat_type", "p2p")
        if chat_id:
            info["chats"] = {chat_id: {"name": chat_id, "type": chat_type}}
        return info
    
    async def deliver(self, target: dict, message: OutgoingMessage):
        """Deliver message to Feishu target"""
        try:
            if not self.lark_client:
                logger.error("Feishu client not initialized, cannot deliver")
                return
            
            if not message:
                return
            if not message.text and not getattr(message, "attachments", None):
                logger.warning("Empty message, skipping Feishu deliver")
                return
            text = message.text or "（见附件）"
            
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest, CreateMessageRequestBody
            )
            
            chat_id = target.get("chat_id")
            user_id = target.get("user_id")
            
            # Prefer chat_id (reply to original chat)
            receive_id = chat_id or user_id
            receive_id_type = "chat_id" if chat_id else "open_id"
            
            if not receive_id:
                logger.warning(f"No valid target for Feishu delivery: {target}")
                return
            
            content = json.dumps({"text": text})
            
            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("text")
                    .content(content)
                    .build()) \
                .build()
            
            response = await self.lark_client.im.v1.message.acreate(request)
            
            if response.success():
                logger.info(f"Delivered Feishu message to {receive_id_type}:{receive_id}")
            else:
                logger.error(f"Feishu send failed: code={response.code}, msg={response.msg}")
            
            # 发送附件
            import os
            from lark_oapi.api.im.v1 import (
                CreateFileRequest, CreateFileRequestBody,
            )
            for file_path in (message.attachments or []):
                if not os.path.exists(file_path):
                    logger.warning(f"Feishu attachment not found: {file_path}")
                    continue
                try:
                    # 1. 上传文件
                    filename = os.path.basename(file_path)
                    with open(file_path, 'rb') as f:
                        upload_req = CreateFileRequest.builder() \
                            .request_body(CreateFileRequestBody.builder()
                                .file_type("stream")
                                .file_name(filename)
                                .file(f)
                                .build()) \
                            .build()
                        upload_resp = await self.lark_client.im.v1.file.acreate(upload_req)
                    
                    if not upload_resp.success():
                        logger.error(f"Feishu file upload failed: {upload_resp.code} {upload_resp.msg}")
                        continue
                    
                    file_key = upload_resp.data.file_key
                    
                    # 2. 发送文件消息
                    file_content = json.dumps({"file_key": file_key})
                    file_msg_req = CreateMessageRequest.builder() \
                        .receive_id_type(receive_id_type) \
                        .request_body(CreateMessageRequestBody.builder()
                            .receive_id(receive_id)
                            .msg_type("file")
                            .content(file_content)
                            .build()) \
                        .build()
                    file_msg_resp = await self.lark_client.im.v1.message.acreate(file_msg_req)
                    
                    if file_msg_resp.success():
                        logger.info(f"Delivered Feishu attachment: {filename}")
                    else:
                        logger.error(f"Feishu file message failed: {file_msg_resp.code} {file_msg_resp.msg}")
                except Exception as e:
                    logger.error(f"Failed to send Feishu attachment {file_path}: {e}")
                
        except Exception as e:
            logger.error(f"Error delivering Feishu message: {e}", exc_info=True)
    
    async def _cleanup(self):
        """Cleanup connections"""
        # ws_client doesn't have a clean stop, but it's daemon thread so it'll die
        self.ws_client = None
        self.lark_client = None
        self._ws_thread = None
    
    async def stop(self):
        """Stop Feishu Bot"""
        logger.info("Stopping Feishu Bot...")
        self._should_stop = True
        await self._cleanup()
        logger.info("Feishu Bot stopped")
