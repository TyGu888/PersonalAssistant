"""
Config Manager Tools - 运行时配置热更新

提供:
- config_get: 读取配置值
- config_set: 设置配置值（仅内存，不写文件）
- switch_llm_profile: 切换 LLM Profile
- reload_skills: 重新加载 Skills
"""

import logging
from tools.registry import registry

logger = logging.getLogger(__name__)


def _get_by_path(d: dict, path: str):
    """按 dot-separated path 读取嵌套 dict 的值"""
    keys = path.split(".")
    current = d
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _set_by_path(d: dict, path: str, value):
    """按 dot-separated path 设置嵌套 dict 的值，中间层自动创建"""
    keys = path.split(".")
    current = d
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _cast_value(value: str):
    """将字符串自动转换为合适的 Python 类型"""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


@registry.register(
    name="config",
    description=(
        "Runtime configuration management. "
        "Actions: get (read config value), set (write config value, memory only), "
        "switch_profile (change LLM profile), reload_skills (rescan skills directory)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "set", "switch_profile", "reload_skills"],
                "description": "Action to perform"
            },
            "path": {"type": "string", "description": "Config path, dot-separated (for get/set, e.g. 'llm.active')"},
            "value": {"type": "string", "description": "Value to set (for set, auto-converts type)"},
            "profile_name": {"type": "string", "description": "LLM profile name (for switch_profile)"}
        },
        "required": ["action"]
    }
)
async def config(action: str, path: str = None, value: str = None, profile_name: str = None, context=None) -> str:
    """Runtime configuration management."""

    if action == "get":
        if not path:
            return "错误: get 操作需要 path"
        if context is None:
            return "错误: 缺少上下文信息"
        agent_loop = context.get("agent_loop")
        if agent_loop is None:
            return "错误: 无法获取 AgentLoop 实例"
        val = _get_by_path(agent_loop.config, path)
        if val is None:
            return f"配置项 '{path}' 不存在"
        return f"{path} = {val}"

    elif action == "set":
        if not path:
            return "错误: set 操作需要 path"
        if value is None:
            return "错误: set 操作需要 value"
        if context is None:
            return "错误: 缺少上下文信息"
        agent_loop = context.get("agent_loop")
        if agent_loop is None:
            return "错误: 无法获取 AgentLoop 实例"
        converted = _cast_value(value)
        _set_by_path(agent_loop.config, path, converted)
        logger.info(f"配置已更新: {path} = {converted}")
        # 副作用：切换 llm.active 时重建 Agent
        if path == "llm.active":
            return await _apply_llm_switch(agent_loop, str(converted))
        # 副作用：computer_use 配置变更时重新初始化
        if path.startswith("computer_use."):
            return _apply_computer_use_reinit(agent_loop, path, converted)
        return f"已设置 {path} = {converted}（仅运行时生效）"

    elif action == "switch_profile":
        if not profile_name:
            return "错误: switch_profile 操作需要 profile_name"
        if context is None:
            return "错误: 缺少上下文信息"
        agent_loop = context.get("agent_loop")
        if agent_loop is None:
            return "错误: 无法获取 AgentLoop 实例"
        # 验证 profile 存在
        profiles = agent_loop.config.get("llm_profiles", {})
        if profile_name not in profiles:
            available = ", ".join(profiles.keys())
            return f"错误: Profile '{profile_name}' 不存在。可用: {available}"
        # 更新配置
        agent_loop.config.setdefault("llm", {})["active"] = profile_name
        return await _apply_llm_switch(agent_loop, profile_name)

    elif action == "reload_skills":
        return await _reload_skills(context=context)

    else:
        return f"错误: 未知 action '{action}'。可用: get, set, switch_profile, reload_skills"


def _apply_computer_use_reinit(agent_loop, path: str, value) -> str:
    """computer_use 配置变更后重新初始化"""
    try:
        from tools.computer_use import init_computer_use
        init_computer_use(agent_loop.config)
        enabled = agent_loop.config.get("computer_use", {}).get("enabled", False)
        status = "已初始化" if enabled else "已禁用"
        logger.info(f"Computer Use reinit: {status}")
        return f"已设置 {path} = {value} — Computer Use {status}"
    except Exception as e:
        logger.warning(f"Computer Use reinit failed: {e}")
        return f"已设置 {path} = {value}（Computer Use 重新初始化失败: {e}）"


async def _apply_llm_switch(agent_loop, profile_name: str) -> str:
    """切换 LLM Profile 后重建 DefaultAgent"""
    profiles = agent_loop.config.get("llm_profiles", {})
    if profile_name not in profiles:
        available = ", ".join(profiles.keys())
        return f"错误: Profile '{profile_name}' 不存在。可用: {available}"

    try:
        from agent.default import DefaultAgent
        from skills.loader import get_skill_summaries

        llm_config = agent_loop._get_llm_config()

        # 保留当前 skill_summaries
        skill_summaries = get_skill_summaries(
            getattr(agent_loop, "_skills", {})
        )

        agent_loop.agents["default"] = DefaultAgent(
            llm_config=llm_config,
            skill_summaries=skill_summaries
        )

        model = llm_config.get("model", "unknown")
        logger.info(f"LLM Profile 已切换: {profile_name} (model={model})")
        return f"已切换到 {profile_name}，模型: {model}"

    except Exception as e:
        logger.error(f"切换 LLM Profile 失败: {e}", exc_info=True)
        return f"错误: 切换失败 - {e}"


async def _reload_skills(context=None) -> str:
    """Reload all skills from the skills/ directory."""
    if context is None:
        return "错误: 缺少上下文信息"

    agent_loop = context.get("agent_loop")
    if agent_loop is None:
        return "错误: 无法获取 AgentLoop 实例"

    try:
        from skills.loader import load_skills, get_skill_summaries

        skills_config = agent_loop.config.get("skills", {})
        skills_dir = skills_config.get("dir", "./skills")

        # 重新加载
        skills = load_skills(skills_dir)

        # 应用 overrides
        overrides = skills_config.get("overrides", {})
        for skill_name, override_cfg in overrides.items():
            if not override_cfg.get("enabled", True):
                skills.pop(skill_name, None)

        agent_loop._skills = skills
        skill_summaries = get_skill_summaries(skills)

        # 更新 DefaultAgent 的 skill_summaries
        default_agent = agent_loop.agents.get("default")
        if default_agent:
            default_agent.skill_summaries = skill_summaries

        names = [s["name"] for s in skill_summaries]
        logger.info(f"Skills 已重新加载: {names}")
        return f"已重新加载 {len(names)} 个 Skills: {', '.join(names)}"

    except Exception as e:
        logger.error(f"重新加载 Skills 失败: {e}", exc_info=True)
        return f"错误: 重新加载失败 - {e}"
