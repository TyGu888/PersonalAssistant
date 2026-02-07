"""
AgentRuntime - Agent 的运行时上下文

职责：
1. 持有 MemoryManager，管理会话历史和长期记忆
2. 提供 session 上下文加载（历史 + 记忆 + Token 截断）
3. 提供消息保存
4. 处理 person_id 解析（跨渠道身份统一）

设计：Agent 自己管理记忆，而不是 Engine/Gateway 帮它管理。
"""

import logging
from typing import Optional

from memory.manager import MemoryManager
from tools.registry import registry

logger = logging.getLogger(__name__)


class AgentRuntime:
    """
    Agent 运行时环境
    
    封装 Agent 执行所需的所有运行时依赖：
    - MemoryManager: 会话历史 + 长期记忆
    - ToolRegistry: Tool 注册表（引用全局 registry）
    - 身份解析: channel:user_id → person_id
    """
    
    def __init__(self, memory_config: dict, llm_config: dict, data_dir: str = "./data", identity_mode: str = "single_owner"):
        """
        初始化运行时
        
        参数:
        - memory_config: 记忆配置 (max_context_messages, max_context_tokens 等)
        - llm_config: LLM 配置 (用于 MemoryManager 的 embedding)
        - data_dir: 数据目录
        - identity_mode: 身份模式 "single_owner" | "multi_user"
        """
        self.memory = MemoryManager(data_dir, llm_config, memory_config)
        self.memory_config = memory_config
        self.identity_mode = identity_mode
        self.registry = registry
        
        logger.info(f"AgentRuntime initialized (identity_mode={identity_mode})")
    
    def resolve_person_id(self, channel: str, user_id: str) -> str:
        """
        把渠道 user_id 映射到统一的 person_id
        
        single_owner 模式：所有用户映射到 "owner"
        multi_user 模式：保持 "{channel}:{user_id}"
        """
        if self.identity_mode == "single_owner":
            return "owner"
        return f"{channel}:{user_id}"
    
    async def load_context(self, session_id: str, query: str, person_id: str) -> dict:
        """
        加载会话上下文（历史 + 相关记忆，带 Token 截断）
        
        参数:
        - session_id: 会话 ID
        - query: 当前查询（用于记忆检索）
        - person_id: 统一身份标识
        
        返回: {"history": list[ChatMessage], "memories": list[str]}
        """
        max_context_messages = self.memory_config.get("max_context_messages", 20)
        context = await self.memory.get_context(
            session_id=session_id,
            query=query,
            person_id=person_id,
            history_limit=max_context_messages
        )
        return context
    
    def save_message(self, session_id: str, role: str, content: str):
        """
        保存消息到会话历史
        
        参数:
        - session_id: 会话 ID
        - role: "user" | "assistant"
        - content: 消息内容
        """
        self.memory.save_message(session_id, role, content)
    
    def get_tool_schemas(self, tool_names: list[str]) -> list[dict]:
        """获取 Tool schemas"""
        return self.registry.get_schemas(tool_names)
    
    def get_tool_context(self, person_id: str, session_id: str, msg_context: dict) -> dict:
        """
        构建 Tool 执行时的上下文（依赖注入）
        
        注意：在新架构中，tool_context 不再包含 engine 引用。
        需要 engine 功能的地方（如 send_push）通过 Dispatcher 实现。
        """
        return {
            "runtime": self,          # AgentRuntime 引用
            "memory": self.memory,
            "person_id": person_id,
            "session_id": session_id,
            "msg_context": msg_context,
            "pending_attachments": [],
            # dispatcher and scheduler are injected by AgentLoop after this call
        }
