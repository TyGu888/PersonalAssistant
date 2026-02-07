"""
QQ Actions - QQ 原生操作工具

提供 QQ 特有的操作：
- qq_add_reaction: 给消息添加 emoji 表情回复
- qq_pin_message: 设置精华消息/公告

通过 context["channel_manager"].channels["qq"].bot_client 获取
"""

import logging
from tools.registry import registry

logger = logging.getLogger(__name__)


async def get_qq_api(context):
    """Get QQ bot API from context"""
    if context is None:
        raise ValueError("Missing context")
    channel_manager = context.get("channel_manager")
    if channel_manager is None:
        raise ValueError("Missing channel_manager")
    qq_channel = channel_manager.channels.get("qq")
    if qq_channel is None:
        raise ValueError("QQ channel not registered")
    client = getattr(qq_channel, "bot_client", None)
    if client is None:
        raise ValueError("QQ bot client not initialized")
    return client.api


@registry.register(
    name="qq_add_reaction",
    description="给 QQ 频道消息添加 emoji 表情回复",
    parameters={
        "type": "object",
        "properties": {
            "channel_id": {"type": "string", "description": "频道 ID"},
            "message_id": {"type": "string", "description": "消息 ID (raw.msg_id)"},
            "emoji_type": {"type": "string", "description": "Emoji 类型 ID (1=系统表情, 2=Emoji)"},
            "emoji_id": {"type": "string", "description": "Emoji ID (系统表情编号或 unicode)"}
        },
        "required": ["channel_id", "message_id", "emoji_type", "emoji_id"]
    }
)
async def qq_add_reaction(channel_id: str, message_id: str, emoji_type: str, emoji_id: str, context=None) -> str:
    try:
        api = await get_qq_api(context)
        await api.put_reaction(channel_id=channel_id, message_id=message_id,
                               type=emoji_type, id=emoji_id)
        return "已添加表情回复"
    except Exception as e:
        logger.error(f"qq_add_reaction failed: {e}", exc_info=True)
        return f"添加表情失败: {str(e)}"


@registry.register(
    name="qq_pin_message",
    description="在 QQ 频道中置顶/精华消息",
    parameters={
        "type": "object",
        "properties": {
            "channel_id": {"type": "string", "description": "频道 ID"},
            "message_id": {"type": "string", "description": "消息 ID"}
        },
        "required": ["channel_id", "message_id"]
    }
)
async def qq_pin_message(channel_id: str, message_id: str, context=None) -> str:
    try:
        api = await get_qq_api(context)
        await api.put_pin(channel_id=channel_id, message_id=message_id)
        return "已置顶消息"
    except Exception as e:
        logger.error(f"qq_pin_message failed: {e}", exc_info=True)
        return f"置顶失败: {str(e)}"
