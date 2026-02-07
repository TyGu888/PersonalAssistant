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
    description="向指定渠道发送消息。省略 channel/user_id 时默认回复当前对话（原始频道/线程）。跨渠道发送需指定 channel + channel_id 或 user_id。",
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要发送的消息文本"
            },
            "channel": {
                "type": "string",
                "description": "目标渠道名称，如 'discord', 'telegram'。省略则使用当前对话渠道"
            },
            "channel_id": {
                "type": "string",
                "description": "目标频道/群组/线程 ID（Discord channel_id, Telegram chat_id）。省略则从当前对话继承"
            },
            "user_id": {
                "type": "string",
                "description": "目标用户 ID（DM 场景）。省略则从当前对话继承"
            }
        },
        "required": ["text"]
    }
)
async def send_message(text: str, channel: str = None, channel_id: str = None, user_id: str = None, context=None) -> str:
    """
    通过 Dispatcher 向指定 Channel 投递消息
    
    target 构建逻辑：
    - 未指定任何路由参数 → 从当前消息的 raw 继承完整 target（回到原始频道/线程）
    - 指定了 channel_id/user_id → 使用指定值覆盖
    """
    if not context:
        return "错误：缺少执行上下文"
    
    dispatcher = context.get("dispatcher")
    if not dispatcher:
        return "错误：Dispatcher 未配置，无法发送消息"
    
    msg_context = context.get("msg_context", {})
    
    # 确定 channel
    if channel is None:
        channel = msg_context.get("channel")
    if not channel:
        return "错误：无法确定目标渠道（请指定 channel）"
    
    # 构建 target：从当前消息 raw 继承，指定值覆盖
    raw = msg_context.get("raw", {}) if isinstance(msg_context.get("raw"), dict) else {}
    target = {**raw, "user_id": msg_context.get("user_id")}
    
    # 显式指定的值覆盖继承值
    if channel_id is not None:
        target["channel_id"] = channel_id
        target["chat_id"] = channel_id  # Telegram 兼容
    if user_id is not None:
        target["user_id"] = user_id
    
    try:
        message = OutgoingMessage(text=text)
        await dispatcher.send_to_channel(channel, target, message)
        return f"消息已发送到 {channel} (target: channel_id={target.get('channel_id')}, user_id={target.get('user_id')})"
    except Exception as e:
        logger.error(f"send_message failed: {e}", exc_info=True)
        return f"发送失败: {str(e)}"
