"""
WeCom Actions - 企业微信原生操作工具

提供企业微信特有操作（普通发送请用 send_message）：
- wecom_reply_message: 向用户或群聊发送应用消息（回复场景）
- wecom_send_to_group: 向指定群聊发送消息
- wecom_upload_media: 上传临时素材（图片/文件/语音），返回 media_id
- wecom_download_media: 根据 media_id 下载素材到本地路径

通过 context["channel_manager"].channels["wecom"] 获取 WeComChannel。
"""

import logging
import os
from typing import Optional

import httpx

from tools.registry import registry

logger = logging.getLogger(__name__)

WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"


async def get_wecom_channel(context):
    """Get WeComChannel from context"""
    if context is None:
        raise ValueError("Missing context")
    channel_manager = context.get("channel_manager")
    if channel_manager is None:
        raise ValueError("Missing channel_manager")
    ch = channel_manager.channels.get("wecom")
    if ch is None:
        raise ValueError("WeCom channel not registered")
    return ch


@registry.register(
    name="wecom_reply_message",
    description="企业微信：向用户或群聊发送应用消息（回复到原会话）",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "用户 userid（单聊时必填）"},
            "chat_id": {"type": "string", "description": "群聊 chatid（群聊时必填，与 user_id 二选一）"},
            "text": {"type": "string", "description": "消息内容"}
        },
        "required": ["text"]
    }
)
async def wecom_reply_message(
    text: str, user_id: Optional[str] = None, chat_id: Optional[str] = None, context=None
) -> str:
    try:
        ch = await get_wecom_channel(context)
        token = await ch.get_access_token()
        if not token:
            return "WeCom 未就绪或 token 无效"
        if chat_id:
            url = f"{WECOM_API_BASE}/appchat/send"
            body = {"chatid": chat_id, "msgtype": "text", "text": {"content": text[:2048]}}
        elif user_id:
            url = f"{WECOM_API_BASE}/message/send"
            body = {
                "touser": user_id,
                "msgtype": "text",
                "agentid": int(ch.agent_id) if ch.agent_id.isdigit() else ch.agent_id,
                "text": {"content": text[:2048]},
            }
        else:
            return "请提供 user_id（单聊）或 chat_id（群聊）"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params={"access_token": token}, json=body)
        data = resp.json()
        if data.get("errcode") != 0:
            return f"发送失败: {data.get('errmsg')}"
        return "已发送"
    except Exception as e:
        logger.error(f"wecom_reply_message failed: {e}", exc_info=True)
        return f"发送失败: {str(e)}"


@registry.register(
    name="wecom_send_to_group",
    description="企业微信：向指定群聊发送文本消息",
    parameters={
        "type": "object",
        "properties": {
            "chat_id": {"type": "string", "description": "群聊 chatid"},
            "text": {"type": "string", "description": "消息内容"}
        },
        "required": ["chat_id", "text"]
    }
)
async def wecom_send_to_group(chat_id: str, text: str, context=None) -> str:
    try:
        ch = await get_wecom_channel(context)
        token = await ch.get_access_token()
        if not token:
            return "WeCom 未就绪或 token 无效"
        url = f"{WECOM_API_BASE}/appchat/send"
        body = {"chatid": chat_id, "msgtype": "text", "text": {"content": text[:2048]}}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params={"access_token": token}, json=body)
        data = resp.json()
        if data.get("errcode") != 0:
            return f"发送失败: {data.get('errmsg')}"
        return "已发送到群聊"
    except Exception as e:
        logger.error(f"wecom_send_to_group failed: {e}", exc_info=True)
        return f"发送失败: {str(e)}"


@registry.register(
    name="wecom_upload_media",
    description="企业微信：上传临时素材（图片/文件/语音），返回 media_id，用于发送或回复",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "本地文件路径"},
            "media_type": {"type": "string", "description": "类型: image / voice / video / file", "default": "file"}
        },
        "required": ["file_path"]
    }
)
async def wecom_upload_media(
    file_path: str, media_type: str = "file", context=None
) -> str:
    try:
        ch = await get_wecom_channel(context)
        token = await ch.get_access_token()
        if not token:
            return "WeCom 未就绪或 token 无效"
        path = os.path.abspath(file_path)
        if not os.path.exists(path) or not os.path.isfile(path):
            return f"文件不存在: {file_path}"
        url = f"{WECOM_API_BASE}/media/upload"
        params = {"access_token": token, "type": media_type}
        with open(path, "rb") as f:
            content = f.read()
        files = {"media": (os.path.basename(path), content)}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, params=params, files=files)
        data = resp.json()
        if data.get("errcode") != 0:
            return f"上传失败: {data.get('errmsg')}"
        return f"media_id: {data.get('media_id')}"
    except Exception as e:
        logger.error(f"wecom_upload_media failed: {e}", exc_info=True)
        return f"上传失败: {str(e)}"


@registry.register(
    name="wecom_download_media",
    description="企业微信：根据 media_id 下载临时素材到本地文件",
    parameters={
        "type": "object",
        "properties": {
            "media_id": {"type": "string", "description": "临时素材 media_id"},
            "save_path": {"type": "string", "description": "保存到的本地路径（含文件名）"}
        },
        "required": ["media_id", "save_path"]
    }
)
async def wecom_download_media(media_id: str, save_path: str, context=None) -> str:
    try:
        ch = await get_wecom_channel(context)
        token = await ch.get_access_token()
        if not token:
            return "WeCom 未就绪或 token 无效"
        url = f"{WECOM_API_BASE}/media/get"
        params = {"access_token": token, "media_id": media_id}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return f"下载失败: HTTP {resp.status_code}"
        # 可能返回 JSON 错误或二进制内容
        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            data = resp.json()
            if data.get("errcode") != 0:
                return f"下载失败: {data.get('errmsg')}"
        path = os.path.abspath(save_path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(resp.content)
        return f"已保存到 {path}"
    except Exception as e:
        logger.error(f"wecom_download_media failed: {e}", exc_info=True)
        return f"下载失败: {str(e)}"
