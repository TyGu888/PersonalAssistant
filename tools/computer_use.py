"""
Computer Use Tools - GUI 操作工具

提供两个层次:
1. computer_action (高层): 发出完整任务指令，Grounding Engine 自主执行全部步骤
2. screenshot / click / type_text / hotkey / scroll (低层): 精确控制，主 Agent 逐步操作

主 Agent 应优先使用 computer_action。低层工具仅在需要精确控制或 computer_action 失败时使用。
"""

import logging
from tools.registry import registry
from tools.computer.actions import ActionBackend
from tools.computer.grounding import GroundingEngine

logger = logging.getLogger(__name__)

# 延迟初始化：Gateway 启动时通过 init_computer_use(config) 创建
_engine: GroundingEngine | None = None
_actions: ActionBackend | None = None


def init_computer_use(config: dict):
    """由 Gateway 启动时调用，初始化 GroundingEngine 和 ActionBackend"""
    global _engine, _actions
    cu_config = config.get("computer_use", {})
    if not cu_config.get("enabled", False):
        logger.info("Computer Use disabled in config")
        return

    screenshot_dir = cu_config.get("screenshot_dir", "/tmp/agent_screenshots")
    _actions = ActionBackend(screenshot_dir=screenshot_dir)
    _engine = GroundingEngine(config)
    logger.info("Computer Use initialized")


def _get_engine() -> GroundingEngine:
    if _engine is None:
        raise RuntimeError("Computer Use not initialized. Set computer_use.enabled=true in config.")
    return _engine


def _get_actions() -> ActionBackend:
    if _actions is None:
        raise RuntimeError("Computer Use not initialized. Set computer_use.enabled=true in config.")
    return _actions


# ===== 高层工具 =====

@registry.register(
    name="computer_action",
    description=(
        "执行完整的 GUI 任务。可以是复合指令，如 '打开微信，找到张三，发送消息：明天开会'。"
        "内部自主完成所有子步骤（截图、定位、操作、验证），无需逐步指挥。"
        "返回文本结果摘要。失败时会附带截图供诊断。"
        "优先用 Shell 处理能用命令行搞定的事。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "GUI 任务描述，自然语言。"
                    "可以是简单的（'点击搜索框'）也可以是复合的（'打开 Chrome，搜索天气预报'）。"
                )
            },
            "max_steps": {
                "type": "integer",
                "description": "最大执行步数，默认 15",
                "default": 15
            },
        },
        "required": ["task"]
    }
)
async def computer_action(task: str, max_steps: int = 15, context=None) -> str:
    engine = _get_engine()
    result = await engine.execute_task(task=task, max_steps=max_steps)
    return result.to_text()


# ===== 低层工具 =====

@registry.register(
    name="screenshot",
    description="截取当前屏幕截图。返回图片文件路径。用于查看屏幕当前状态。",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    }
)
async def screenshot(context=None) -> str:
    actions = _get_actions()
    path = actions.screenshot()
    return f"截图已保存: {path}"


@registry.register(
    name="gui_click",
    description="在屏幕指定坐标点击。配合 screenshot 使用。",
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X 坐标"},
            "y": {"type": "integer", "description": "Y 坐标"},
            "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "鼠标按钮，默认 left"},
            "double": {"type": "boolean", "description": "是否双击，默认 false"}
        },
        "required": ["x", "y"]
    }
)
async def gui_click(x: int, y: int, button: str = "left", double: bool = False, context=None) -> str:
    actions = _get_actions()
    actions.click(x, y, button=button, clicks=2 if double else 1)
    return f"已点击 ({x}, {y})"


@registry.register(
    name="gui_type",
    description="输入文本（支持中文）。先确保目标输入框已获得焦点。",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要输入的文本"},
            "press_enter": {"type": "boolean", "description": "输入后是否按回车，默认 false"}
        },
        "required": ["text"]
    }
)
async def gui_type(text: str, press_enter: bool = False, context=None) -> str:
    actions = _get_actions()
    actions.type_text(text)
    if press_enter:
        actions.hotkey("enter")
    return f"已输入: {text}"


@registry.register(
    name="gui_hotkey",
    description="按下键盘快捷键，如 'cmd+c', 'ctrl+a', 'enter', 'escape'",
    parameters={
        "type": "object",
        "properties": {
            "keys": {"type": "string", "description": "快捷键，用 + 连接，如 'cmd+c', 'alt+tab'"}
        },
        "required": ["keys"]
    }
)
async def gui_hotkey(keys: str, context=None) -> str:
    actions = _get_actions()
    key_list = [k.strip() for k in keys.split("+")]
    actions.hotkey(*key_list)
    return f"已按下: {keys}"


@registry.register(
    name="gui_scroll",
    description="滚动屏幕",
    parameters={
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "description": "滚动量，默认 3"}
        },
        "required": ["direction"]
    }
)
async def gui_scroll(direction: str, amount: int = 3, context=None) -> str:
    actions = _get_actions()
    actions.scroll(direction, amount)
    return f"已滚动: {direction} {amount}"
