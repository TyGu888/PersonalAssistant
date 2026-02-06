"""
Channel Tools - Agent 主动通讯工具

让 Agent 能够主动通过 Channel 发送消息，
而不仅仅是回复用户的消息。

使用场景：
- Agent 需要在另一个 Channel 上通知用户
- Agent 需要在群里主动发消息
- Agent 需要向特定用户推送信息
"""

from tools.registry import registry
from core.types import OutgoingMessage
import logging

logger = logging.getLogger(__name__)


@registry.register(
    name="send_message",
    description="主动向指定渠道和用户发送消息。可用于跨渠道通知、主动推送等场景。",
    parameters={
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "description": "目标渠道名称，如 'discord', 'telegram', 'websocket'"
            },
            "user_id": {
                "type": "string",
                "description": "目标用户 ID 或频道/群组 ID"
            },
            "text": {
                "type": "string",
                "description": "要发送的消息文本"
            }
        },
        "required": ["channel", "user_id", "text"]
    }
)
async def send_message(channel: str, user_id: str, text: str, context=None) -> str:
    """
    通过 Dispatcher 向指定 Channel 发送消息
    
    context 中需要有 "dispatcher" 键（由 AgentLoop 注入）
    """
    if not context:
        return "错误：缺少执行上下文"
    
    dispatcher = context.get("dispatcher")
    if not dispatcher:
        return "错误：Dispatcher 未配置，无法发送消息"
    
    try:
        message = OutgoingMessage(text=text)
        await dispatcher.send_to_channel(channel, user_id, message)
        return f"消息已发送到 {channel}:{user_id}"
    except Exception as e:
        logger.error(f"send_message failed: {e}", exc_info=True)
        return f"发送失败: {str(e)}"
