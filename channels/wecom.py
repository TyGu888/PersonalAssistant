"""
WeCom (企业微信) Channel - 自建应用回调模式

通过 HTTP 回调接收消息（需公网 URL），通过 REST API 发送消息。
- GET/POST /wecom/callback 由本 Channel 注册到 Gateway 的 FastAPI
- access_token 自动刷新（7200s 有效期，每 6000s 刷新）
- 支持单聊、群聊（应用推送消息到群聊）

配置:
- corp_id: 企业 ID
- app_secret: 应用 Secret
- agent_id: 应用 AgentId
- token: 回调配置的 Token
- encoding_aes_key: 回调配置的 EncodingAESKey（43 字符）
- allowed_users: 白名单 userid 列表，空则全部接收
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Optional, Any

import httpx

from channels.base import BaseChannel
from channels.wecom_crypto import WXBizMsgCrypt, WXBizMsgCryptError
from core.types import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)

WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
TOKEN_REFRESH_INTERVAL = 6000  # 刷新间隔（秒），略小于 7200


def _text(elem: Optional[ET.Element]) -> str:
    if elem is None or elem.text is None:
        return ""
    return (elem.text or "").strip()


class WeComChannel(BaseChannel):
    """
    企业微信自建应用 Channel。
    接收消息通过 FastAPI 回调路由，发送消息通过 REST API。
    """

    def __init__(
        self,
        corp_id: str,
        app_secret: str,
        agent_id: str,
        token: str,
        encoding_aes_key: str,
        allowed_users: list = None,
        app: Any = None,
    ):
        BaseChannel.__init__(self)
        self.corp_id = corp_id
        self.app_secret = app_secret
        self.agent_id = agent_id
        self.token = token
        self.encoding_aes_key = encoding_aes_key
        self.allowed_users = set(str(u) for u in (allowed_users or []))
        self._app = app  # FastAPI app，用于注册回调路由

        self._access_token: Optional[str] = None
        self._token_lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._crypto: Optional[WXBizMsgCrypt] = None
        self.is_running = False

    async def get_access_token(self) -> Optional[str]:
        """供 tools 等调用，获取当前 access_token；若即将过期会触发刷新"""
        async with self._token_lock:
            return self._access_token

    def set_app(self, app: Any):
        """由 ChannelManager 注入 FastAPI app（在 init_channels 之后调用）"""
        self._app = app

    async def start(self):
        """获取初始 token、启动刷新任务、注册回调路由，然后阻塞直到 stop"""
        self.is_running = True
        self._stop_event.clear()

        try:
            self._crypto = WXBizMsgCrypt(
                self.token, self.encoding_aes_key, self.corp_id
            )
        except WXBizMsgCryptError as e:
            logger.error(f"WeCom crypto init failed: {e}")
            self.is_running = False
            return

        await self._refresh_token()
        if not self._access_token:
            logger.error("WeCom: failed to get initial access_token")
            self.is_running = False
            return

        self._refresh_task = asyncio.create_task(
            self._token_refresh_loop(), name="wecom-token-refresh"
        )

        if self._app:
            self._register_routes()
            logger.info("WeCom Channel: callback routes registered at /wecom/callback")
        else:
            logger.warning("WeCom Channel: no FastAPI app set, callback not registered")

        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            if self._refresh_task and not self._refresh_task.done():
                self._refresh_task.cancel()
                try:
                    await self._refresh_task
                except asyncio.CancelledError:
                    pass
            self.is_running = False
            logger.info("WeCom Channel exited")

    async def _token_refresh_loop(self):
        while self.is_running:
            try:
                await asyncio.sleep(TOKEN_REFRESH_INTERVAL)
                if not self.is_running:
                    break
                await self._refresh_token()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"WeCom token refresh error: {e}")

    async def _refresh_token(self) -> bool:
        async with self._token_lock:
            url = f"{WECOM_API_BASE}/gettoken"
            params = {"corpid": self.corp_id, "corpsecret": self.app_secret}
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(url, params=params)
                data = resp.json()
            except Exception as e:
                logger.error(f"WeCom gettoken request failed: {e}")
                return False
            if data.get("errcode") != 0:
                logger.error(f"WeCom gettoken error: {data.get('errmsg')}")
                return False
            self._access_token = data.get("access_token")
            logger.debug("WeCom access_token refreshed")
            return True

    def _register_routes(self):
        """在 FastAPI app 上注册 GET/POST /wecom/callback"""
        from fastapi import Request, Query
        from fastapi.responses import PlainTextResponse

        app = self._app
        channel_ref = self

        @app.get("/wecom/callback", tags=["WeCom"])
        async def wecom_verify(
            msg_signature: str = Query(..., alias="msg_signature"),
            timestamp: str = Query(..., alias="timestamp"),
            nonce: str = Query(..., alias="nonce"),
            echostr: str = Query(..., alias="echostr"),
        ):
            """企业微信回调 URL 验证"""
            ok, result = channel_ref._crypto.verify_url(
                msg_signature, timestamp, nonce, echostr
            )
            if not ok:
                return PlainTextResponse(result, status_code=400)
            return PlainTextResponse(result)

        @app.post("/wecom/callback", tags=["WeCom"])
        async def wecom_callback(
            request: Request,
            msg_signature: str = Query(..., alias="msg_signature"),
            timestamp: str = Query(..., alias="timestamp"),
            nonce: str = Query(..., alias="nonce"),
        ):
            """企业微信消息回调"""
            body = await request.body()
            post_data = body.decode("utf-8", errors="replace")
            ok, decrypted = channel_ref._crypto.decrypt_msg(
                msg_signature, timestamp, nonce, post_data
            )
            if not ok:
                logger.warning(f"WeCom decrypt failed: {decrypted}")
                return PlainTextResponse("decrypt error", status_code=400)
            await channel_ref._handle_callback_xml(decrypted)
            return PlainTextResponse("")

    async def _handle_callback_xml(self, xml_str: str):
        """解析解密后的 XML，构造 IncomingMessage 并 publish"""
        try:
            root = ET.fromstring(xml_str)
            msg_type = _text(root.find("MsgType"))
            from_user = _text(root.find("FromUserName"))
            if not from_user:
                return

            if self.allowed_users and from_user not in self.allowed_users:
                return

            # 事件类型不处理为对话消息（可后续扩展）
            if msg_type == "event":
                event = _text(root.find("Event"))
                logger.debug(f"WeCom event: {event}")
                return

            # 文本
            text = _text(root.find("Content"))
            if msg_type == "image":
                media_id = _text(root.find("MediaId"))
                pic_url = _text(root.find("PicUrl"))
                if not text:
                    text = "[图片]"
                raw_extra = {"media_id": media_id, "pic_url": pic_url}
            elif msg_type == "voice":
                media_id = _text(root.find("MediaId"))
                if not text:
                    text = "[语音]"
                raw_extra = {"media_id": media_id}
            elif msg_type == "file":
                media_id = _text(root.find("MediaId"))
                if not text:
                    text = "[文件]"
                raw_extra = {"media_id": media_id}
            else:
                raw_extra = {}

            # 群聊：ChatId 存在则为群
            chat_id_elem = root.find("ChatId")
            if chat_id_elem is not None and (chat_id_elem.text or "").strip():
                chat_id = (chat_id_elem.text or "").strip()
                is_group = True
                group_id = chat_id
            else:
                is_group = False
                group_id = None

            raw = {
                "FromUserName": from_user,
                "MsgType": msg_type,
                "AgentID": _text(root.find("AgentID")),
                "MsgId": _text(root.find("MsgId")),
                **raw_extra,
            }
            if is_group:
                raw["ChatId"] = group_id

            incoming = IncomingMessage(
                channel="wecom",
                user_id=from_user,
                text=text or "",
                is_group=is_group,
                group_id=group_id,
                thread_id=None,
                reply_expected=True,
                raw=raw,
            )
            logger.info(f"WeCom message from {from_user}: {text[:50]}...")
            await self.publish_message(incoming)
        except Exception as e:
            logger.error(f"WeCom handle callback error: {e}", exc_info=True)

    async def deliver(self, target: dict, message: OutgoingMessage):
        """投递消息到企业微信（单聊用应用消息 API，群聊用应用推送消息到群聊 API）"""
        if not message:
            return
        if not message.text and not getattr(message, "attachments", None):
            logger.warning("WeCom: empty message, skip deliver")
            return
        text = message.text or "（见附件）"

        chat_id = target.get("ChatId") or target.get("chat_id")
        user_id = target.get("user_id") or target.get("FromUserName")

        async with self._token_lock:
            token = self._access_token
        if not token:
            logger.error("WeCom: no access_token, cannot deliver")
            return

        if chat_id:
            await self._send_to_chat(token, chat_id, text, message)
        elif user_id:
            await self._send_to_user(token, user_id, text, message)
        else:
            logger.warning(f"WeCom: no valid target in {target}")

    async def _send_to_user(
        self, token: str, user_id: str, text: str, message: OutgoingMessage
    ):
        url = f"{WECOM_API_BASE}/message/send"
        params = {"access_token": token}
        body = {
            "touser": user_id,
            "msgtype": "text",
            "agentid": int(self.agent_id) if self.agent_id.isdigit() else self.agent_id,
            "text": {"content": text[:2048]},
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params=params, json=body)
        data = resp.json()
        if data.get("errcode") != 0:
            logger.error(f"WeCom send to user error: {data.get('errmsg')}")
            raise RuntimeError(data.get("errmsg", "send failed"))
        logger.info(f"WeCom delivered to user {user_id}")

        attachments = getattr(message, "attachments", None) or []
        for path in attachments:
            await self._upload_and_send_file(token, user_id, None, path)

    async def _send_to_chat(
        self, token: str, chat_id: str, text: str, message: OutgoingMessage
    ):
        url = f"{WECOM_API_BASE}/appchat/send"
        params = {"access_token": token}
        body = {
            "chatid": chat_id,
            "msgtype": "text",
            "text": {"content": text[:2048]},
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params=params, json=body)
        data = resp.json()
        if data.get("errcode") != 0:
            logger.error(f"WeCom send to chat error: {data.get('errmsg')}")
            raise RuntimeError(data.get("errmsg", "send failed"))
        logger.info(f"WeCom delivered to chat {chat_id}")

        attachments = getattr(message, "attachments", None) or []
        for path in attachments:
            await self._upload_and_send_file(token, None, chat_id, path)

    async def _upload_and_send_file(
        self, token: str, user_id: Optional[str], chat_id: Optional[str], file_path: str
    ):
        import os
        path = os.path.abspath(file_path)
        if not os.path.exists(path):
            logger.warning(f"WeCom attachment not found: {path}")
            return
        upload_url = f"{WECOM_API_BASE}/media/upload"
        params = {"access_token": token, "type": "file"}
        with open(path, "rb") as f:
            file_content = f.read()
        files = {"media": (os.path.basename(path), file_content)}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(upload_url, params=params, files=files)
        data = resp.json()
        if data.get("errcode") != 0:
            logger.error(f"WeCom upload file error: {data.get('errmsg')}")
            return
        media_id = data.get("media_id")
        if not media_id:
            return
        if chat_id:
            send_url = f"{WECOM_API_BASE}/appchat/send"
            body = {"chatid": chat_id, "msgtype": "file", "file": {"media_id": media_id}}
        else:
            send_url = f"{WECOM_API_BASE}/message/send"
            body = {
                "touser": user_id,
                "msgtype": "file",
                "agentid": int(self.agent_id) if self.agent_id.isdigit() else self.agent_id,
                "file": {"media_id": media_id},
            }
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(send_url, params={"access_token": token}, json=body)

    def extract_contact_info(self, msg: IncomingMessage) -> dict:
        raw = msg.raw or {}
        info = {}
        if msg.user_id:
            info["dm_users"] = {str(msg.user_id): {"name": raw.get("FromUserName", msg.user_id)}}
        chat_id = raw.get("ChatId")
        if chat_id:
            info["channels"] = {chat_id: {"name": chat_id, "type": "group"}}
        return info

    async def stop(self):
        self._stop_event.set()
        logger.info("WeCom Channel stop requested")
