"""
QQ Channel - QQ 频道/群/C2C 机器人

使用 qq-botpy (tencent-connect/botpy) SDK
通过 WebSocket 连接 QQ 开放平台

配置:
- appid: QQ Bot App ID
- secret: QQ Bot App Secret
- allowed_users: 白名单用户 ID 列表
"""

import asyncio
import logging
import os
import threading
from typing import Optional

from channels.base import BaseChannel, ReconnectMixin
from core.types import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class QQChannel(BaseChannel, ReconnectMixin):

    def __init__(self, appid: str, secret: str, allowed_users: list = None):
        BaseChannel.__init__(self)
        self.__init_reconnect__()
        self.appid = appid
        self.secret = secret
        self.allowed_users = set(str(u) for u in (allowed_users or []))
        self.bot_client = None  # botpy.Client instance
        self._bot_thread = None
        self._loop = None  # reference to main asyncio event loop

    async def start(self):
        """Start QQ Bot with reconnect loop"""
        self.is_running = True
        self._should_stop = False
        self._loop = asyncio.get_event_loop()

        while not self._should_stop:
            try:
                self._connect()

                # Wait for bot thread to finish
                while not self._should_stop and self._bot_thread and self._bot_thread.is_alive():
                    await asyncio.sleep(1)

                if self._should_stop:
                    break
                logger.warning("QQ Bot disconnected")

            except asyncio.CancelledError:
                logger.info("QQ Bot start cancelled")
                break
            except Exception as e:
                logger.error(f"QQ Bot connection error: {e}", exc_info=True)
                self._cleanup_sync()
                if not await self._wait_for_reconnect("QQChannel"):
                    break

        self.is_running = False
        logger.info("QQ Bot exited")

    def _connect(self):
        """Create bot client and start in background thread"""
        try:
            import botpy
            from botpy.message import Message, GroupMessage, C2CMessage
        except ImportError:
            raise ImportError("qq-botpy not installed. Run: pip install qq-botpy")

        # We need to create the Client subclass with references to this channel
        channel_ref = self

        class _QQBotClient(botpy.Client):
            async def on_ready(self):
                logger.info(f"QQ Bot logged in as {self.robot.name}")
                channel_ref._reset_reconnect_state()
                # Startup scan
                asyncio.run_coroutine_threadsafe(
                    channel_ref._startup_scan(self), channel_ref._loop
                )
                # Report connected
                if channel_ref._contact_callback:
                    channel_ref._contact_callback({"status": "connected", "bot_name": self.robot.name})

            async def on_at_message_create(self, message: Message):
                """Guild channel: bot @mentioned"""
                user_id = str(message.author.id)
                if channel_ref.allowed_users and user_id not in channel_ref.allowed_users:
                    return

                text = message.content or ""
                # Remove @mention text
                import re
                text = re.sub(r'<@!\d+>\s*', '', text).strip()

                raw = {
                    "channel_id": message.channel_id,
                    "guild_id": message.guild_id,
                    "msg_id": message.id,
                    "user_id": user_id,
                    "msg_type": "guild",
                }

                incoming = IncomingMessage(
                    channel="qq",
                    user_id=user_id,
                    text=text,
                    is_group=True,
                    group_id=message.channel_id,
                    reply_expected=True,
                    raw=raw,
                )

                logger.info(f"QQ guild message from {user_id}: {text[:50]}...")
                fut = asyncio.run_coroutine_threadsafe(
                    channel_ref.publish_message(incoming), channel_ref._loop
                )
                fut.result(timeout=30)

            async def on_group_at_message_create(self, message: GroupMessage):
                """Group: bot @mentioned"""
                user_id = str(message.author.member_openid)

                raw = {
                    "group_openid": message.group_openid,
                    "msg_id": message.id,
                    "user_id": user_id,
                    "msg_type": "group",
                }

                incoming = IncomingMessage(
                    channel="qq",
                    user_id=user_id,
                    text=message.content or "",
                    is_group=True,
                    group_id=message.group_openid,
                    reply_expected=True,
                    raw=raw,
                )

                logger.info(f"QQ group message from {user_id}: {incoming.text[:50]}...")
                fut = asyncio.run_coroutine_threadsafe(
                    channel_ref.publish_message(incoming), channel_ref._loop
                )
                fut.result(timeout=30)

            async def on_c2c_message_create(self, message: C2CMessage):
                """C2C direct message"""
                user_id = str(message.author.user_openid)

                raw = {
                    "user_openid": user_id,
                    "msg_id": message.id,
                    "user_id": user_id,
                    "msg_type": "c2c",
                }

                incoming = IncomingMessage(
                    channel="qq",
                    user_id=user_id,
                    text=message.content or "",
                    is_group=False,
                    reply_expected=True,
                    raw=raw,
                )

                logger.info(f"QQ C2C from {user_id}: {incoming.text[:50]}...")
                fut = asyncio.run_coroutine_threadsafe(
                    channel_ref.publish_message(incoming), channel_ref._loop
                )
                fut.result(timeout=30)

        import botpy
        intents = botpy.Intents(public_guild_messages=True, public_messages=True)
        self.bot_client = _QQBotClient(intents=intents)

        # Run blocking client.run() in background thread
        self._bot_thread = threading.Thread(
            target=self._run_bot_blocking,
            name="qq-bot",
            daemon=True
        )
        self._bot_thread.start()
        logger.info("QQ Bot starting in background thread...")

    def _run_bot_blocking(self):
        """Run blocking bot in thread"""
        try:
            self.bot_client.run(appid=self.appid, secret=self.secret)
        except Exception as e:
            logger.error(f"QQ Bot error: {e}", exc_info=True)

    async def _startup_scan(self, bot_client):
        """Scan guilds and channels after connection"""
        if not self._contact_callback:
            return
        try:
            scan_info = {"guilds": {}}

            guilds_result = await bot_client.api.me_guilds()
            if guilds_result:
                for guild in guilds_result:
                    guild_id = guild.get("id", "") if isinstance(guild, dict) else getattr(guild, "id", "")
                    guild_name = guild.get("name", "") if isinstance(guild, dict) else getattr(guild, "name", "")
                    guild_data = {"name": guild_name, "channels": {}}

                    try:
                        channels_result = await bot_client.api.get_channels(guild_id=guild_id)
                        if channels_result:
                            for ch in channels_result:
                                ch_id = ch.get("id", "") if isinstance(ch, dict) else getattr(ch, "id", "")
                                ch_name = ch.get("name", "") if isinstance(ch, dict) else getattr(ch, "name", "")
                                guild_data["channels"][str(ch_id)] = {"name": ch_name}
                    except Exception as e:
                        logger.debug(f"Failed to get channels for guild {guild_id}: {e}")

                    scan_info["guilds"][str(guild_id)] = guild_data

            self._contact_callback(scan_info)
            logger.info(f"QQ startup scan: {len(scan_info['guilds'])} guild(s)")
        except Exception as e:
            logger.warning(f"QQ startup scan failed: {e}")

    def extract_contact_info(self, msg: IncomingMessage) -> dict:
        """Extract contact info from QQ message"""
        raw = msg.raw or {}
        info = {}
        msg_type = raw.get("msg_type", "")

        if msg_type == "guild":
            guild_id = raw.get("guild_id", "")
            channel_id = raw.get("channel_id", "")
            if guild_id:
                info["guilds"] = {
                    str(guild_id): {
                        "name": str(guild_id),
                        "channels": {str(channel_id): {"name": str(channel_id)}} if channel_id else {}
                    }
                }
        elif msg_type == "group":
            group_openid = raw.get("group_openid", "")
            if group_openid:
                info["groups"] = {group_openid: {"name": group_openid}}
        elif msg_type == "c2c":
            user_openid = raw.get("user_openid", "")
            if user_openid:
                info["dm_users"] = {user_openid: {"name": user_openid}}

        return info

    async def deliver(self, target: dict, message: OutgoingMessage):
        """Deliver message to QQ target"""
        try:
            if not self.bot_client:
                logger.error("QQ bot client not initialized, cannot deliver")
                return

            if not message:
                return
            if not message.text and not getattr(message, "attachments", None):
                logger.warning("Empty message, skipping QQ deliver")
                return
            text = message.text or "（见附件）"

            api = self.bot_client.api
            msg_type = target.get("msg_type", "")
            msg_id = target.get("msg_id")

            if msg_type == "guild" and target.get("channel_id"):
                # Guild channel message
                kwargs = {"channel_id": target["channel_id"], "content": text}
                if msg_id:
                    kwargs["msg_id"] = msg_id
                await api.post_message(**kwargs)
                logger.info(f"Delivered QQ guild message to channel {target['channel_id']}")

            elif msg_type == "group" and target.get("group_openid"):
                # Group message
                kwargs = {
                    "group_openid": target["group_openid"],
                    "msg_type": 0,
                    "content": text,
                }
                if msg_id:
                    kwargs["msg_id"] = msg_id
                await api.post_group_message(**kwargs)
                logger.info(f"Delivered QQ group message to {target['group_openid']}")

            elif target.get("user_openid"):
                # C2C direct message
                kwargs = {
                    "openid": target["user_openid"],
                    "msg_type": 0,
                    "content": text,
                }
                if msg_id:
                    kwargs["msg_id"] = msg_id
                await api.post_c2c_message(**kwargs)
                logger.info(f"Delivered QQ C2C to {target['user_openid']}")

            else:
                logger.warning(f"No valid target for QQ delivery: {target}")
            
            # QQ Bot API 不支持发送任意文件附件，记录警告
            if message.attachments:
                logger.warning(
                    f"QQ channel does not support file attachments. "
                    f"Skipping {len(message.attachments)} attachment(s): "
                    f"{[os.path.basename(p) for p in message.attachments]}"
                )

        except Exception as e:
            logger.error(f"Error delivering QQ message: {e}", exc_info=True)

    def _cleanup_sync(self):
        """Sync cleanup"""
        self.bot_client = None
        self._bot_thread = None

    async def stop(self):
        """Stop QQ Bot"""
        logger.info("Stopping QQ Bot...")
        self._should_stop = True
        # bot_client.run() is blocking in a daemon thread, it'll die
        self._cleanup_sync()
        logger.info("QQ Bot stopped")
