"""
Slack Actions - Slack 原生操作工具

提供 Slack 特有的操作（普通的发送消息请用 tools/channel.py 的 send_message）：
- slack_reply_in_thread: 在消息的 Thread 中回复
- slack_add_reaction: 给消息添加 emoji 反应
- slack_pin_message: 置顶消息

通过 context["channel_manager"].channels["slack"].client 获取 Slack WebClient
"""

import logging
from tools.registry import registry

logger = logging.getLogger(__name__)


async def get_slack_client(context):
    """Get Slack WebClient from context"""
    if context is None:
        raise ValueError("Missing context")
    channel_manager = context.get("channel_manager")
    if channel_manager is None:
        raise ValueError("Missing channel_manager")
    slack_channel = channel_manager.channels.get("slack")
    if slack_channel is None:
        raise ValueError("Slack channel not registered")
    client = getattr(slack_channel, "client", None)
    if client is None:
        raise ValueError("Slack client not initialized")
    return client


@registry.register(
    name="slack_reply_in_thread",
    description="在 Slack 消息的 Thread 中回复",
    parameters={
        "type": "object",
        "properties": {
            "channel_id": {"type": "string", "description": "Slack channel ID"},
            "thread_ts": {"type": "string", "description": "Thread timestamp (from message raw.thread_ts or raw.ts)"},
            "text": {"type": "string", "description": "回复内容"}
        },
        "required": ["channel_id", "thread_ts", "text"]
    }
)
async def slack_reply_in_thread(channel_id: str, thread_ts: str, text: str, context=None) -> str:
    try:
        client = await get_slack_client(context)
        await client.chat_postMessage(channel=channel_id, text=text, thread_ts=thread_ts)
        return f"已在 Thread {thread_ts} 中回复"
    except Exception as e:
        logger.error(f"slack_reply_in_thread failed: {e}", exc_info=True)
        return f"回复失败: {str(e)}"


@registry.register(
    name="slack_add_reaction",
    description="给 Slack 消息添加 emoji 反应",
    parameters={
        "type": "object",
        "properties": {
            "channel_id": {"type": "string", "description": "Slack channel ID"},
            "timestamp": {"type": "string", "description": "消息的 timestamp (raw.ts)"},
            "emoji": {"type": "string", "description": "Emoji 名称，如 'thumbsup', 'heart', 'eyes'（不含冒号）"}
        },
        "required": ["channel_id", "timestamp", "emoji"]
    }
)
async def slack_add_reaction(channel_id: str, timestamp: str, emoji: str, context=None) -> str:
    try:
        client = await get_slack_client(context)
        await client.reactions_add(channel=channel_id, timestamp=timestamp, name=emoji)
        return f"已添加反应 :{emoji}:"
    except Exception as e:
        logger.error(f"slack_add_reaction failed: {e}", exc_info=True)
        return f"添加反应失败: {str(e)}"


@registry.register(
    name="slack_pin_message",
    description="在 Slack 频道中置顶消息",
    parameters={
        "type": "object",
        "properties": {
            "channel_id": {"type": "string", "description": "Slack channel ID"},
            "timestamp": {"type": "string", "description": "消息的 timestamp (raw.ts)"}
        },
        "required": ["channel_id", "timestamp"]
    }
)
async def slack_pin_message(channel_id: str, timestamp: str, context=None) -> str:
    try:
        client = await get_slack_client(context)
        await client.pins_add(channel=channel_id, timestamp=timestamp)
        return f"已置顶消息"
    except Exception as e:
        logger.error(f"slack_pin_message failed: {e}", exc_info=True)
        return f"置顶失败: {str(e)}"
