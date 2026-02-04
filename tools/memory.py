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
    name="memory_search",
    description="搜索长期记忆：在回答关于过去对话、用户偏好、承诺、环境信息等问题前，先搜索记忆",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询"},
            "scope": {
                "type": "string",
                "enum": ["all", "global", "personal"],
                "description": "搜索范围：all=全部, global=仅全局, personal=仅个人",
                "default": "all"
            },
            "max_results": {"type": "integer", "description": "最大返回数", "default": 5}
        },
        "required": ["query"]
    }
)
async def memory_search(query: str, scope: str = "all", max_results: int = 5, context=None) -> str:
    """
    搜索长期记忆
    
    参数:
    - query: 搜索查询文本
    - scope: 搜索范围 (all/global/personal)
    - max_results: 最大返回数量
    - context: 注入的上下文 {"memory": MemoryManager, "person_id": str, ...}
    
    返回: 格式化的搜索结果
    """
    if context is None:
        return "错误: 缺少上下文信息"
    
    memory = context.get("memory")
    if memory is None:
        return "错误: 无法获取 MemoryManager 实例"
    
    # 获取 person_id（从 context 中获取，由 engine 计算好）
    person_id = context.get("person_id", "owner")
    
    try:
        # 根据 scope 决定查询范围
        if scope == "global":
            # 只查全局记忆：使用一个不存在的 person_id 配合 include_global=True
            # 这样只会匹配 scope="global" 的记忆
            # 注意：ChromaDB 查询逻辑是 scope="global" OR (scope="personal" AND person_id=xxx)
            # 所以用 include_global=True 但 person_id 设为空，只会匹配全局记忆
            results = await memory.global_mem.search(
                person_id="__global_only__",
                query=query,
                top_k=max_results,
                include_global=True
            )
        elif scope == "personal":
            # 只查个人记忆
            results = await memory.global_mem.search(
                person_id=person_id,
                query=query,
                top_k=max_results,
                include_global=False
            )
        else:
            # all: 查全部（个人 + 全局）
            results = await memory.global_mem.search(
                person_id=person_id,
                query=query,
                top_k=max_results,
                include_global=True
            )
        
        if not results:
            return f"未找到与 \"{query}\" 相关的记忆"
        
        # 格式化返回结果
        lines = [f"找到 {len(results)} 条相关记忆："]
        for i, item in enumerate(results, 1):
            scope_label = "全局" if item.scope == "global" else "个人"
            type_label = _get_type_label(item.type)
            lines.append(f"{i}. [{scope_label}][{type_label}] {item.content}")
        
        return "\n".join(lines)
    
    except Exception as e:
        logger.error(f"搜索记忆失败: {e}", exc_info=True)
        return f"错误: 搜索记忆失败 - {str(e)}"


@registry.register(
    name="memory_add",
    description="添加长期记忆：当发现重要信息时主动记录（用户偏好、事实、承诺、环境信息等）",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "记忆内容"},
            "memory_type": {
                "type": "string",
                "enum": ["preference", "fact", "event", "commitment", "env"],
                "description": "记忆类型：preference=偏好, fact=事实, event=事件, commitment=承诺, env=环境信息"
            },
            "scope": {
                "type": "string",
                "enum": ["global", "personal"],
                "description": "记忆范围：global=全局（所有对话可见）, personal=个人",
                "default": "personal"
            }
        },
        "required": ["content", "memory_type"]
    }
)
async def memory_add(content: str, memory_type: str, scope: str = "personal", context=None) -> str:
    """
    添加长期记忆
    
    参数:
    - content: 记忆内容
    - memory_type: 记忆类型 (preference/fact/event/commitment/env)
    - scope: 记忆范围 (global/personal)
    - context: 注入的上下文 {"memory": MemoryManager, "person_id": str, "msg_context": dict, ...}
    
    返回: 确认消息
    """
    if context is None:
        return "错误: 缺少上下文信息"
    
    memory = context.get("memory")
    if memory is None:
        return "错误: 无法获取 MemoryManager 实例"
    
    # 获取 person_id 和 session_id
    person_id = context.get("person_id", "owner")
    
    # 从 msg_context 获取 session 信息，用于 source_session
    msg_context = context.get("msg_context", {})
    channel = msg_context.get("channel", "unknown")
    user_id = msg_context.get("user_id", "unknown")
    source_session = f"{channel}:{user_id}"
    
    try:
        # 调用 GlobalMemory.add() 添加记忆
        memory_id = await memory.global_mem.add(
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
