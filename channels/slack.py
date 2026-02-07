"""
Slack Channel - Slack Bot (Socket Mode)

使用 slack-bolt AsyncApp + AsyncSocketModeHandler
通过 WebSocket (Socket Mode) 连接，无需公网 HTTP endpoint

配置:
- bot_token: xoxb-... (Bot User OAuth Token)
- app_token: xapp-... (App-Level Token, connections:write scope)
- allowed_users: 白名单用户 ID 列表 (Slack user IDs like U01234)
"""

import asyncio
import logging
from typing import Optional

from channels.base import BaseChannel, ReconnectMixin
from core.types import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class SlackChannel(BaseChannel, ReconnectMixin):
    
    def __init__(self, bot_token: str, app_token: str, allowed_users: list = None):
        BaseChannel.__init__(self)
        self.__init_reconnect__()
        self.bot_token = bot_token
        self.app_token = app_token
        self.allowed_users = set(str(u) for u in (allowed_users or []))
        self.app = None  # AsyncApp
        self.handler = None  # AsyncSocketModeHandler
        self.client = None  # WebClient (for API calls from tools)
    
    async def start(self):
        """Start Slack Bot with reconnect loop"""
        self.is_running = True
        self._should_stop = False
        
        while not self._should_stop:
            try:
                await self._connect()
                # handler.start_async() blocks until disconnected
                await self.handler.start_async()
                
                if self._should_stop:
                    break
                logger.warning("Slack Bot disconnected")
            except asyncio.CancelledError:
                logger.info("Slack Bot start cancelled")
                break
            except Exception as e:
                logger.error(f"Slack Bot connection error: {e}", exc_info=True)
                await self._cleanup()
                if not await self._wait_for_reconnect("SlackChannel"):
                    break
        
        self.is_running = False
        logger.info("Slack Bot exited")
    
    async def _connect(self):
        """Create and configure the Slack app"""
        try:
            from slack_bolt.async_app import AsyncApp
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        except ImportError:
            raise ImportError("slack-bolt not installed. Run: pip install 'slack-bolt[async]'")
        
        self.app = AsyncApp(token=self.bot_token)
        self.client = self.app.client
        
        # Register event handlers
        @self.app.event("message")
        async def handle_message(event, say, context):
            await self._on_message(event)
        
        @self.app.event("app_mention")
        async def handle_mention(event, say, context):
            await self._on_mention(event)
        
        self.handler = AsyncSocketModeHandler(self.app, self.app_token)
        
        # Reset reconnect state on successful connection
        self._reset_reconnect_state()
        logger.info("Slack Bot connected (Socket Mode)")
        
        # Startup scan
        await self._startup_scan()
    
    async def _startup_scan(self):
        """Scan workspaces and channels after connection"""
        if not self.client or not self._contact_callback:
            return
        try:
            scan_info = {"status": "connected", "channels": {}, "dm_users": {}}
            
            # List channels the bot is in
            result = await self.client.conversations_list(
                types="public_channel,private_channel",
                limit=200
            )
            for ch in result.get("channels", []):
                if ch.get("is_member"):  # Bot is in this channel
                    scan_info["channels"][ch["id"]] = {
                        "name": ch.get("name", ch["id"]),
                        "type": "private" if ch.get("is_private") else "public"
                    }
            
            self._contact_callback(scan_info)
            logger.info(f"Slack startup scan: {len(scan_info['channels'])} channel(s)")
        except Exception as e:
            logger.warning(f"Slack startup scan failed: {e}")
    
    async def _on_message(self, event: dict):
        """Handle regular message events (DMs and channels)"""
        # Skip bot messages and message_changed subtypes
        if event.get("bot_id") or event.get("subtype"):
            return
        
        user_id = event.get("user", "")
        if not user_id:
            return
        
        # Check whitelist
        if self.allowed_users and str(user_id) not in self.allowed_users:
            return
        
        channel_id = event.get("channel", "")
        channel_type = event.get("channel_type", "")  # "im" for DM, "channel"/"group" for channels
        is_dm = channel_type == "im"
        
        # In channels, only process if it's a DM (non-mention messages in channels handled by _on_mention)
        # Actually, in Slack, "message" event in channels fires for ALL messages.
        # We only want to process: DMs (always) and channel messages (record but don't reply)
        is_group = not is_dm
        
        raw = {
            "channel_id": channel_id,
            "thread_ts": event.get("thread_ts") or event.get("ts"),
            "ts": event.get("ts"),
            "user_id": user_id,
            "team_id": event.get("team"),
            "channel_type": channel_type,
        }
        
        incoming = IncomingMessage(
            channel="slack",
            user_id=str(user_id),
            text=event.get("text", ""),
            is_group=is_group,
            group_id=channel_id if is_group else None,
            reply_expected=is_dm,  # DMs always reply; channel messages only if mentioned
            raw=raw,
        )
        
        if is_dm:
            logger.info(f"Slack DM from {user_id}: {incoming.text[:50]}...")
        
        await self.publish_message(incoming)
    
    async def _on_mention(self, event: dict):
        """Handle app_mention events (bot @mentioned in channels)"""
        user_id = event.get("user", "")
        if not user_id:
            return
        
        if self.allowed_users and str(user_id) not in self.allowed_users:
            return
        
        channel_id = event.get("channel", "")
        text = event.get("text", "")
        
        # Remove the @mention prefix (Slack includes <@BOT_ID> in text)
        # Clean up: remove <@UXXXXX> patterns
        import re
        text = re.sub(r'<@[A-Z0-9]+>\s*', '', text).strip()
        
        raw = {
            "channel_id": channel_id,
            "thread_ts": event.get("thread_ts") or event.get("ts"),
            "ts": event.get("ts"),
            "user_id": user_id,
            "team_id": event.get("team"),
            "channel_type": "channel",
        }
        
        incoming = IncomingMessage(
            channel="slack",
            user_id=str(user_id),
            text=text,
            is_group=True,
            group_id=channel_id,
            reply_expected=True,
            raw=raw,
        )
        
        logger.info(f"Slack mention from {user_id} in {channel_id}: {text[:50]}...")
        await self.publish_message(incoming)
    
    def extract_contact_info(self, msg: IncomingMessage) -> dict:
        """Extract contact info from Slack message"""
        raw = msg.raw or {}
        info = {}
        channel_id = raw.get("channel_id")
        channel_type = raw.get("channel_type", "")
        
        if channel_type == "im" and msg.user_id:
            info["dm_users"] = {str(msg.user_id): {"name": str(msg.user_id)}}
        elif channel_id:
            info["channels"] = {channel_id: {"name": channel_id, "type": channel_type}}
        
        return info
    
    async def deliver(self, target: dict, message: OutgoingMessage):
        """Deliver message to Slack target"""
        try:
            if not self.client:
                logger.error("Slack client not initialized, cannot deliver")
                return
            
            if not message or not message.text:
                logger.warning("Empty message, skipping Slack deliver")
                return
            
            channel_id = target.get("channel_id")
            thread_ts = target.get("thread_ts")
            user_id = target.get("user_id")
            
            if channel_id:
                kwargs = {"channel": channel_id, "text": message.text}
                if thread_ts:
                    kwargs["thread_ts"] = thread_ts
                await self.client.chat_postMessage(**kwargs)
                logger.info(f"Delivered Slack message to channel {channel_id}")
            elif user_id:
                # Open DM and send
                result = await self.client.conversations_open(users=[user_id])
                dm_channel = result["channel"]["id"]
                await self.client.chat_postMessage(channel=dm_channel, text=message.text)
                logger.info(f"Delivered Slack DM to user {user_id}")
            else:
                logger.warning(f"No valid target for Slack delivery: {target}")
                
        except Exception as e:
            logger.error(f"Error delivering Slack message: {e}", exc_info=True)
    
    async def _cleanup(self):
        """Cleanup connection"""
        try:
            if self.handler:
                await self.handler.close_async()
        except Exception as e:
            logger.debug(f"Error closing Slack handler: {e}")
        self.handler = None
        self.app = None
        self.client = None
    
    async def stop(self):
        """Stop Slack Bot"""
        logger.info("Stopping Slack Bot...")
        self._should_stop = True
        await self._cleanup()
        logger.info("Slack Bot stopped")
