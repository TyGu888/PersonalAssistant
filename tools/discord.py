"""
Discord Tools - Discord 频道操作工具

提供:
- discord_send_message: 发送消息
- discord_reply_message: 回复消息
- discord_add_reaction: 添加反应
- discord_create_thread: 创建 Thread
"""

import logging
from tools.registry import registry

logger = logging.getLogger(__name__)


async def get_discord_client(context):
    """
    从 context 获取 discord client
    
    通过 context["engine"].channels["discord"].client 获取
    
    异常:
    - ValueError: 如果 context、engine、discord channel 或 client 不存在
    """
    if context is None:
        raise ValueError("缺少上下文信息")
    
    engine = context.get("engine")
    if engine is None:
        raise ValueError("无法获取 engine 实例")
    
    discord_channel = engine.channels.get("discord")
    if discord_channel is None:
        raise ValueError("Discord channel 未启用")
    
    if discord_channel.client is None:
        raise ValueError("Discord client 未连接")
    
    return discord_channel.client


@registry.register(
    name="discord_send_message",
    description="发送消息到指定 Discord 频道",
    parameters={
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "目标频道 ID"
            },
            "content": {
                "type": "string",
                "description": "消息内容"
            }
        },
        "required": ["channel_id", "content"]
    }
)
async def discord_send_message(channel_id: str, content: str, context=None) -> str:
    """
    发送消息到指定频道
    
    参数:
    - channel_id: 目标频道 ID
    - content: 消息内容
    
    返回: "已发送消息到频道 {channel_name} (消息 ID: {message_id})"
    """
    client = await get_discord_client(context)
    
    channel = client.get_channel(int(channel_id))
    if channel is None:
        raise ValueError(f"找不到频道 {channel_id}")
    
    message = await channel.send(content=content)
    
    channel_name = getattr(channel, 'name', channel_id)
    logger.info(f"Sent message to channel {channel_name} (ID: {message.id})")
    
    return f"已发送消息到频道 {channel_name} (消息 ID: {message.id})"


@registry.register(
    name="discord_reply_message",
    description="回复 Discord 中的某条消息",
    parameters={
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "频道 ID"
            },
            "message_id": {
                "type": "string",
                "description": "要回复的消息 ID"
            },
            "content": {
                "type": "string",
                "description": "回复内容"
            }
        },
        "required": ["channel_id", "message_id", "content"]
    }
)
async def discord_reply_message(channel_id: str, message_id: str, content: str, context=None) -> str:
    """
    回复某条消息
    
    参数:
    - channel_id: 频道 ID
    - message_id: 要回复的消息 ID
    - content: 回复内容
    
    返回: "已回复消息 {message_id} (回复 ID: {reply_id})"
    """
    client = await get_discord_client(context)
    
    channel = client.get_channel(int(channel_id))
    if channel is None:
        raise ValueError(f"找不到频道 {channel_id}")
    
    message = await channel.fetch_message(int(message_id))
    reply = await message.reply(content=content)
    
    logger.info(f"Replied to message {message_id} with reply ID {reply.id}")
    
    return f"已回复消息 {message_id} (回复 ID: {reply.id})"


@registry.register(
    name="discord_add_reaction",
    description="给 Discord 消息添加反应（emoji）",
    parameters={
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "频道 ID"
            },
            "message_id": {
                "type": "string",
                "description": "消息 ID"
            },
            "emoji": {
                "type": "string",
                "description": "表情符号（如 '👍' 或 ':thumbsup:'）"
            }
        },
        "required": ["channel_id", "message_id", "emoji"]
    }
)
async def discord_add_reaction(channel_id: str, message_id: str, emoji: str, context=None) -> str:
    """
    给消息添加反应（emoji）
    
    参数:
    - channel_id: 频道 ID
    - message_id: 消息 ID
    - emoji: 表情符号（如 "👍" 或 ":thumbsup:"）
    
    返回: "已添加反应 {emoji} 到消息 {message_id}"
    """
    client = await get_discord_client(context)
    
    channel = client.get_channel(int(channel_id))
    if channel is None:
        raise ValueError(f"找不到频道 {channel_id}")
    
    message = await channel.fetch_message(int(message_id))
    await message.add_reaction(emoji)
    
    logger.info(f"Added reaction {emoji} to message {message_id}")
    
    return f"已添加反应 {emoji} 到消息 {message_id}"


@registry.register(
    name="discord_create_thread",
    description="在 Discord 消息下创建 Thread",
    parameters={
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "频道 ID"
            },
            "message_id": {
                "type": "string",
                "description": "消息 ID（从哪条消息创建 thread）"
            },
            "name": {
                "type": "string",
                "description": "Thread 名称"
            },
            "auto_archive_duration": {
                "type": "integer",
                "description": "自动归档时间（分钟），可选 60, 1440, 4320, 10080"
            }
        },
        "required": ["channel_id", "message_id", "name"]
    }
)
async def discord_create_thread(
    channel_id: str,
    message_id: str,
    name: str,
    auto_archive_duration: int = None,
    context=None
) -> str:
    """
    在消息下创建 Thread
    
    参数:
    - channel_id: 频道 ID
    - message_id: 消息 ID（从哪条消息创建 thread）
    - name: Thread 名称
    - auto_archive_duration: 自动归档时间（分钟），可选 60, 1440, 4320, 10080，默认 1440
    
    返回: "已创建 Thread '{name}' (ID: {thread_id})"
    """
    client = await get_discord_client(context)
    
    channel = client.get_channel(int(channel_id))
    if channel is None:
        raise ValueError(f"找不到频道 {channel_id}")
    
    message = await channel.fetch_message(int(message_id))
    
    # 使用默认值 1440 分钟（1天）如果未指定
    archive_duration = auto_archive_duration or 1440
    
    thread = await message.create_thread(
        name=name,
        auto_archive_duration=archive_duration
    )
    
    logger.info(f"Created thread '{name}' (ID: {thread.id}) on message {message_id}")
    
    return f"已创建 Thread '{name}' (ID: {thread.id})"
