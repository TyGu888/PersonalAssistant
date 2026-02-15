"""
Computer Use Tools - GUI 操作工具

主 Agent 通过 computer_action 发出高层任务指令，GroundingEngine 自主完成全部 GUI 子步骤
（截图 → VisionLLM 规划定位 → PyAutoGUI 执行 → 验证），主 Agent 只看到文本结果。

低层操作（screenshot/click/type/hotkey/scroll）由 GroundingEngine 内部通过 ActionBackend 直接调用，
不注册为主 Agent 工具——主 Agent 无法做像素级推理，暴露这些工具只会浪费 token。

注意: 简单截图推荐用 run_command(command='screencapture -x data/screenshots/shot.png', use_sandbox=false)，
不需要初始化 Computer Use 框架。框架会自动检测 tool result 中的图片路径并展示给 LLM。
"""

import logging
from tools.registry import registry
from tools.computer.grounding import GroundingEngine

logger = logging.getLogger(__name__)

# 延迟初始化：Gateway 启动时通过 init_computer_use(config) 创建
_engine: GroundingEngine | None = None


def init_computer_use(config: dict):
    """由 Gateway 启动时或 config_set 时调用，初始化/重置 GroundingEngine"""
    global _engine
    cu_config = config.get("computer_use", {})
    if not cu_config.get("enabled", False):
        _engine = None
        logger.info("Computer Use disabled in config")
        return

    _engine = GroundingEngine(config)
    logger.info("Computer Use initialized")


def _get_engine() -> GroundingEngine:
    if _engine is None:
        raise RuntimeError("Computer Use not initialized. Set computer_use.enabled=true in config.")
    return _engine



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
