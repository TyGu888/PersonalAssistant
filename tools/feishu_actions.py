"""
Feishu Actions - 飞书原生操作工具

提供飞书特有的操作：
- feishu_reply_message: 回复某条消息 (thread reply)
- feishu_add_reaction: 给消息添加 emoji 反应
- feishu_pin_message: 置顶消息
- feishu_create_chat: 创建群聊

通过 context["channel_manager"].channels["feishu"].lark_client 获取
"""

import json
import logging
from tools.registry import registry

logger = logging.getLogger(__name__)


async def get_feishu_client(context):
    """Get Feishu lark.Client from context"""
    if context is None:
        raise ValueError("Missing context")
    channel_manager = context.get("channel_manager")
    if channel_manager is None:
        raise ValueError("Missing channel_manager")
    feishu_channel = channel_manager.channels.get("feishu")
    if feishu_channel is None:
        raise ValueError("Feishu channel not registered")
    client = getattr(feishu_channel, "lark_client", None)
    if client is None:
        raise ValueError("Feishu client not initialized")
    return client


@registry.register(
    name="feishu_reply_message",
    description="回复飞书消息（在原消息 thread 中回复）",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "要回复的消息 ID (raw.message_id)"},
            "text": {"type": "string", "description": "回复内容"}
        },
        "required": ["message_id", "text"]
    }
)
async def feishu_reply_message(message_id: str, text: str, context=None) -> str:
    try:
        client = await get_feishu_client(context)
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
        
        content = json.dumps({"text": text})
        request = ReplyMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(content)
                .build()) \
            .build()
        
        response = await client.im.v1.message.areply(request)
        if response.success():
            return f"已回复消息 {message_id}"
        return f"回复失败: {response.msg}"
    except Exception as e:
        logger.error(f"feishu_reply_message failed: {e}", exc_info=True)
        return f"回复失败: {str(e)}"


@registry.register(
    name="feishu_add_reaction",
    description="给飞书消息添加 emoji 反应",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "消息 ID"},
            "emoji_type": {"type": "string", "description": "Emoji 类型，如 'THUMBSUP', 'SMILE', 'HEART'"}
        },
        "required": ["message_id", "emoji_type"]
    }
)
async def feishu_add_reaction(message_id: str, emoji_type: str, context=None) -> str:
    try:
        client = await get_feishu_client(context)
        from lark_oapi.api.im.v1 import CreateMessageReactionRequest, CreateMessageReactionRequestBody, Emoji
        
        request = CreateMessageReactionRequest.builder() \
            .message_id(message_id) \
            .request_body(CreateMessageReactionRequestBody.builder()
                .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                .build()) \
            .build()
        
        response = await client.im.v1.message_reaction.acreate(request)
        if response.success():
            return f"已添加反应 {emoji_type}"
        return f"添加反应失败: {response.msg}"
    except Exception as e:
        logger.error(f"feishu_add_reaction failed: {e}", exc_info=True)
        return f"添加反应失败: {str(e)}"


@registry.register(
    name="feishu_pin_message",
    description="在飞书群聊中置顶消息",
    parameters={
        "type": "object",
        "properties": {
            "message_id": {"type": "string", "description": "要置顶的消息 ID"}
        },
        "required": ["message_id"]
    }
)
async def feishu_pin_message(message_id: str, context=None) -> str:
    try:
        client = await get_feishu_client(context)
        from lark_oapi.api.im.v1 import CreatePinRequest, CreatePinRequestBody
        
        request = CreatePinRequest.builder() \
            .request_body(CreatePinRequestBody.builder()
                .message_id(message_id)
                .build()) \
            .build()
        
        response = await client.im.v1.pin.acreate(request)
        if response.success():
            return f"已置顶消息 {message_id}"
        return f"置顶失败: {response.msg}"
    except Exception as e:
        logger.error(f"feishu_pin_message failed: {e}", exc_info=True)
        return f"置顶失败: {str(e)}"


@registry.register(
    name="feishu_create_chat",
    description="创建飞书群聊",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "群聊名称"},
            "description": {"type": "string", "description": "群聊描述（可选）"},
            "user_ids": {"type": "array", "items": {"type": "string"}, "description": "邀请的用户 open_id 列表"}
        },
        "required": ["name"]
    }
)
async def feishu_create_chat(name: str, description: str = "", user_ids: list = None, context=None) -> str:
    try:
        client = await get_feishu_client(context)
        from lark_oapi.api.im.v1 import CreateChatRequest, CreateChatRequestBody
        
        body_builder = CreateChatRequestBody.builder().name(name)
        if description:
            body_builder = body_builder.description(description)
        if user_ids:
            body_builder = body_builder.user_id_list(user_ids)
        
        request = CreateChatRequest.builder() \
            .user_id_type("open_id") \
            .request_body(body_builder.build()) \
            .build()
        
        response = await client.im.v1.chat.acreate(request)
        if response.success():
            chat_id = response.data.chat_id if response.data else "unknown"
            return f"已创建群聊 '{name}' (chat_id: {chat_id})"
        return f"创建群聊失败: {response.msg}"
    except Exception as e:
        logger.error(f"feishu_create_chat failed: {e}", exc_info=True)
        return f"创建群聊失败: {str(e)}"
