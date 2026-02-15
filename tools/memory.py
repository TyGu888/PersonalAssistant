"""
Memory Tools - 记忆操作工具

提供:
- memory_search: Agent 主动搜索长期记忆
- memory_add: Agent 主动添加长期记忆
"""

from tools.registry import registry
import logging

logger = logging.getLogger(__name__)


@registry.register(
    name="memory",
    description=(
        "Long-term memory operations. "
        "Actions: search (find relevant memories), add (store important information)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "add"],
                "description": "Action to perform"
            },
            "query": {"type": "string", "description": "Search query (for search)"},
            "scope": {
                "type": "string",
                "enum": ["all", "global", "personal"],
                "description": "Scope: all/global/personal (for search default: all, for add default: personal)"
            },
            "max_results": {"type": "integer", "description": "Max results (for search, default 5)"},
            "content": {"type": "string", "description": "Memory content (for add)"},
            "memory_type": {
                "type": "string",
                "enum": ["preference", "fact", "event", "commitment", "env"],
                "description": "Memory type (for add)"
            }
        },
        "required": ["action"]
    }
)
async def memory(action: str, query: str = None, scope: str = None, max_results: int = 5,
                 content: str = None, memory_type: str = None, context=None) -> str:
    """Long-term memory operations."""

    if action == "search":
        return await _memory_search(query=query, scope=scope or "all", max_results=max_results, context=context)
    elif action == "add":
        return await _memory_add(content=content, memory_type=memory_type, scope=scope or "personal", context=context)
    else:
        return f"错误: 未知 action '{action}'。可用: search, add"


async def _memory_search(query: str = None, scope: str = "all", max_results: int = 5, context=None) -> str:
    """搜索长期记忆"""
    if context is None:
        return "错误: 缺少上下文信息"

    if not query:
        return "错误: search 操作需要 query"

    mem = context.get("memory")
    if mem is None:
        return "错误: 无法获取 MemoryManager 实例"

    person_id = context.get("person_id", "owner")

    try:
        if scope == "global":
            results = await mem.global_mem.search(
                person_id="__global_only__",
                query=query,
                top_k=max_results,
                include_global=True
            )
        elif scope == "personal":
            results = await mem.global_mem.search(
                person_id=person_id,
                query=query,
                top_k=max_results,
                include_global=False
            )
        else:
            results = await mem.global_mem.search(
                person_id=person_id,
                query=query,
                top_k=max_results,
                include_global=True
            )

        if not results:
            return f"未找到与 \"{query}\" 相关的记忆"

        lines = [f"找到 {len(results)} 条相关记忆："]
        for i, item in enumerate(results, 1):
            scope_label = "全局" if item.scope == "global" else "个人"
            type_label = _get_type_label(item.type)
            lines.append(f"{i}. [{scope_label}][{type_label}] {item.content}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"搜索记忆失败: {e}", exc_info=True)
        return f"错误: 搜索记忆失败 - {str(e)}"


async def _memory_add(content: str = None, memory_type: str = None, scope: str = "personal", context=None) -> str:
    """添加长期记忆"""
    if context is None:
        return "错误: 缺少上下文信息"

    if not content:
        return "错误: add 操作需要 content"
    if not memory_type:
        return "错误: add 操作需要 memory_type"

    mem = context.get("memory")
    if mem is None:
        return "错误: 无法获取 MemoryManager 实例"

    person_id = context.get("person_id", "owner")

    msg_context = context.get("msg_context", {})
    channel = msg_context.get("channel", "unknown")
    user_id = msg_context.get("user_id", "unknown")
    source_session = f"{channel}:{user_id}"

    try:
        memory_id = await mem.global_mem.add(
            person_id=person_id,
            content=content,
            memory_type=memory_type,
            source_session=source_session,
            scope=scope
        )

        type_label = _get_type_label(memory_type)
        scope_label = "全局" if scope == "global" else "个人"

        logger.info(f"添加记忆成功: [{scope_label}][{type_label}] {content[:50]}...")

        return f"已添加{scope_label}记忆 [{type_label}]: {content}"

    except Exception as e:
        logger.error(f"添加记忆失败: {e}", exc_info=True)
        return f"错误: 添加记忆失败 - {str(e)}"


def _get_type_label(memory_type: str) -> str:
    """获取记忆类型的中文标签"""
    type_labels = {
        "preference": "偏好",
        "fact": "事实",
        "event": "事件",
        "commitment": "承诺",
        "env": "环境"
    }
    return type_labels.get(memory_type, memory_type)
