"""
Channel Tools - Agent 主动通讯工具

让 Agent 能够主动通过 Channel 发送消息，
而不仅仅是回复用户的消息。

使用场景：
- Agent 需要在另一个 Channel 上通知用户
- Agent 需要在群里主动发消息
- Agent 需要向特定用户推送信息
- Agent 可查询/管理通讯录（get_contacts、contact_remove）
"""

import json
import logging
from tools.registry import registry
from core.types import OutgoingMessage

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


@registry.register(
    name="get_contacts",
    description="获取当前通讯录（所有已连接渠道的 guild/channel/chat 等）。用于查看可联系的渠道与目标 ID，或为 contact_remove 构造 path。",
    parameters={
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "description": "可选。只查看该渠道的通讯录，如 'discord'；省略则返回全部渠道"
            }
        },
        "required": [],
    },
)
async def get_contacts(channel: str = None, context=None) -> str:
    """返回当前通讯录（JSON 或可读摘要）"""
    if not context:
        return "错误：缺少执行上下文"
    cm = context.get("channel_manager")
    if not cm:
        return "错误：无法获取通讯录（channel_manager 未配置）"
    summary = cm.get_contacts_summary()
    if not summary:
        return "当前通讯录为空（尚无渠道上报或尚未连接）"
    if channel:
        if channel not in summary:
            return f"渠道 {channel} 不在通讯录中。已连接渠道: {list(summary.keys())}"
        summary = {channel: summary[channel]}
    try:
        return json.dumps(summary, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(summary)


@registry.register(
    name="contact_remove",
    description="从通讯录中移除一条记录（如已解散的群/频道）。path 为交替的 key 与 id，例如 Discord 移除 guild: ['guilds','guild_id']，移除该 guild 下某 channel: ['guilds','guild_id','channels','channel_id']。删除前应征得用户确认。",
    parameters={
        "type": "object",
        "properties": {
            "channel_name": {
                "type": "string",
                "description": "渠道名，如 'discord', 'telegram'"
            },
            "path": {
                "type": "array",
                "items": {"type": "string"},
                "description": "路径列表，如 ['guilds','123'] 或 ['guilds','123','channels','456']"
            }
        },
        "required": ["channel_name", "path"],
    },
)
async def contact_remove(channel_name: str, path: list, context=None) -> str:
    """从通讯录中移除指定路径的条目"""
    if not context:
        return "错误：缺少执行上下文"
    cm = context.get("channel_manager")
    if not cm:
        return "错误：无法管理通讯录（channel_manager 未配置）"
    if not path or len(path) % 2 != 0:
        return "错误：path 必须为偶数个元素，如 ['guilds','guild_id'] 或 ['guilds','guild_id','channels','channel_id']"
    ok = cm.remove_contact(channel_name, path)
    if ok:
        return f"已从通讯录移除 {channel_name} 的条目 path={path}"
    return f"移除失败：path 不存在或无效。请先用 get_contacts 查看该渠道结构后构造正确 path。"
