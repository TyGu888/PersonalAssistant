from memory.session import SessionStore
from memory.global_mem import GlobalMemory
from core.types import ChatMessage
from openai import AsyncOpenAI
from typing import Optional
from utils.token_counter import TokenCounter
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


EXTRACT_MEMORIES_PROMPT = '''请从以下对话中提取用户的关键信息，每条信息一行。

关键信息类型：
- preference: 用户偏好（喜好、习惯）
- fact: 用户事实（姓名、职业、技能）
- event: 重要事件（计划、承诺）
- commitment: 用户承诺（目标、决心）

请以 JSON 数组格式输出，每条记忆包含 type 和 content：
[
  {"type": "preference", "content": "用户喜欢早上学习"},
  {"type": "fact", "content": "用户正在学习机器学习"}
]

如果没有值得提取的信息，返回空数组 []

对话内容：
{conversation}'''


class MemoryManager:
    def __init__(self, data_dir: str = "data", llm_config: dict = None, memory_config: dict = None):
        """
        初始化 SessionStore (SQLite) 和 GlobalMemory (ChromaDB)
        
        data_dir 下会创建:
        - sessions.db  (SQLite)
        - chroma/      (ChromaDB)
        
        llm_config: {"api_key": "...", "base_url": "...", "model": "..."}
        memory_config: {"max_context_messages": 20, "max_context_tokens": 8000}
        """
        self.session = SessionStore(f"{data_dir}/sessions.db")
        self.global_mem = GlobalMemory(f"{data_dir}/chroma")
        
        # Memory 配置
        memory_config = memory_config or {}
        self.max_context_messages = memory_config.get("max_context_messages", 20)
        self.max_context_tokens = memory_config.get("max_context_tokens")  # None 表示不启用 token 截断
        
        # Token 计数器
        model = llm_config.get("model", "gpt-4o") if llm_config else "gpt-4o"
        self.token_counter = TokenCounter(model)
        
        # 可选: LLM client 用于记忆提取
        if llm_config and llm_config.get("api_key"):
            client_kwargs = {"api_key": llm_config.get("api_key")}
            if llm_config.get("base_url"):
                client_kwargs["base_url"] = llm_config.get("base_url")
            self.llm_client = AsyncOpenAI(**client_kwargs)
            self.model = llm_config.get("model", "gpt-4o")
        else:
            self.llm_client = None
            self.model = None
    
    # ===== Session 操作 =====
    
    def save_message(self, session_id: str, role: str, content: str):
        """保存消息到 Session"""
        message = ChatMessage(
            role=role,
            content=content,
            timestamp=datetime.utcnow()
        )
        self.session.append(session_id, message)
    
    def get_history(self, session_id: str, limit: int = 50) -> list[dict]:
        """
        获取会话历史（供 HTTP API 使用）
        
        参数:
        - session_id: 会话 ID
        - limit: 最大消息数
        
        返回: 消息列表 [{"role": str, "content": str, "timestamp": str}, ...]
        """
        messages = self.session.get_recent(session_id, n=limit)
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else ""
            }
            for msg in messages
        ]
    
    def clear_session(self, session_id: str):
        """
        清空会话历史（供 HTTP API 使用）
        
        参数:
        - session_id: 会话 ID
        """
        self.session.clear(session_id)
    
    async def get_context(
        self, 
        session_id: str, 
        query: str, 
        user_id: str, 
        history_limit: int = None,
        max_tokens: int = None,
        memory_limit: int = 5
    ) -> dict:
        """
        获取对话上下文（支持基于 Token 截断）
        
        输入:
        - session_id: Session ID
        - query: 当前用户消息（用于 RAG 搜索）
        - user_id: 用户 ID（用于搜索该用户的记忆）
        - history_limit: 历史消息数量限制（可选，默认使用配置值）
        - max_tokens: 历史消息的最大 token 数（可选，默认使用配置值）
        - memory_limit: 相关记忆数量限制
        
        输出:
        {
            "history": [ChatMessage, ...],
            "memories": ["记忆1", "记忆2", ...],
            "token_count": int  # 历史消息的 token 数
        }
        
        截断策略:
        1. 如果指定 max_tokens 或配置了 max_context_tokens，按 token 数截断
        2. 否则按条数截断（向后兼容）
        3. 优先保留最近的消息
        """
        # 使用传入的参数或配置值
        effective_history_limit = history_limit or self.max_context_messages
        effective_max_tokens = max_tokens or self.max_context_tokens
        
        # 获取历史消息（先获取足够多的消息，稍后截断）
        # 如果要按 token 截断，先获取更多消息
        fetch_limit = effective_history_limit * 2 if effective_max_tokens else effective_history_limit
        history = self.session.get_recent(session_id, n=fetch_limit)
        
        # 计算原始 token 数
        history_messages = [{"role": msg.role, "content": msg.content} for msg in history]
        original_tokens = self.token_counter.count_messages(history_messages)
        
        # 按 token 截断（如果启用）
        if effective_max_tokens and original_tokens > effective_max_tokens:
            history = self._truncate_history_by_tokens(history, effective_max_tokens)
            truncated_messages = [{"role": msg.role, "content": msg.content} for msg in history]
            final_tokens = self.token_counter.count_messages(truncated_messages)
            logger.info(
                f"历史消息 token 截断: {original_tokens} -> {final_tokens} tokens, "
                f"{len(history_messages)} -> {len(history)} 条消息"
            )
        else:
            # 按条数截断
            if len(history) > effective_history_limit:
                history = history[-effective_history_limit:]
            final_tokens = self.token_counter.count_messages(
                [{"role": msg.role, "content": msg.content} for msg in history]
            )
        
        # 搜索相关记忆
        memory_items = await self.global_mem.search(user_id, query, top_k=memory_limit)
        
        # 将 MemoryItem 转换为字符串列表
        memories = [item.content for item in memory_items]
        
        return {
            "history": history,
            "memories": memories,
            "token_count": final_tokens
        }
    
    def _truncate_history_by_tokens(self, history: list, max_tokens: int) -> list:
        """
        按 token 数截断历史消息，保留最近的消息
        
        参数:
        - history: ChatMessage 列表（按时间顺序，最早在前）
        - max_tokens: 最大 token 数
        
        返回: 截断后的历史消息列表
        """
        if not history:
            return []
        
        # 从最近的消息开始累计
        kept_messages = []
        current_tokens = 3  # 基础开销
        
        for msg in reversed(history):
            msg_dict = {"role": msg.role, "content": msg.content}
            msg_tokens = self.token_counter.count_messages([msg_dict]) - 3  # 减去基础开销
            
            if current_tokens + msg_tokens <= max_tokens:
                kept_messages.insert(0, msg)
                current_tokens += msg_tokens
            else:
                # 超过限制，停止添加
                break
        
        return kept_messages
    
    # ===== 记忆提取 =====
    
    async def extract_memories(self, session_id: str, user_id: str) -> list[str]:
        """
        从 Session 提取记忆到 GlobalMemory
        
        流程:
        1. 获取 Session 完整历史
        2. 调用 LLM 提取关键信息
        3. 存入 GlobalMemory
        
        输出: 提取的记忆内容列表
        """
        if not self.llm_client:
            raise ValueError("LLM client not initialized. Please provide llm_config in __init__")
        
        # 获取完整对话历史
        messages = self.session.get_all(session_id)
        
        if not messages:
            return []
        
        # 格式化对话内容
        conversation_lines = []
        for msg in messages:
            role_label = "用户" if msg.role == "user" else "助手"
            conversation_lines.append(f"{role_label}: {msg.content}")
        
        conversation_text = "\n".join(conversation_lines)
        
        # 调用 LLM 提取记忆
        prompt = EXTRACT_MEMORIES_PROMPT.format(conversation=conversation_text)
        
        response = await self.llm_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个记忆提取助手，负责从对话中提取关键信息。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        # 解析 JSON 响应
        content = response.choices[0].message.content.strip()
        
        # 尝试解析 JSON（可能包含 markdown 代码块）
        if content.startswith("```"):
            # 移除 markdown 代码块标记
            lines = content.split("\n")
            json_start = None
            json_end = None
            for i, line in enumerate(lines):
                if line.strip().startswith("```"):
                    if json_start is None:
                        json_start = i + 1
                    else:
                        json_end = i
                        break
            
            if json_start is not None and json_end is not None:
                content = "\n".join(lines[json_start:json_end])
            elif json_start is not None:
                content = "\n".join(lines[json_start:])
        
        try:
            memories_data = json.loads(content)
        except json.JSONDecodeError:
            # 如果解析失败，尝试提取 JSON 数组部分
            import re
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                memories_data = json.loads(json_match.group())
            else:
                # 如果还是失败，返回空列表
                return []
        
        # 逐条存入 GlobalMemory
        extracted_contents = []
        for memory_data in memories_data:
            if isinstance(memory_data, dict) and "type" in memory_data and "content" in memory_data:
                memory_type = memory_data["type"]
                memory_content = memory_data["content"]
                
                # 存入 GlobalMemory
                await self.global_mem.add(
                    user_id=user_id,
                    content=memory_content,
                    memory_type=memory_type,
                    source_session=session_id
                )
                extracted_contents.append(memory_content)
        
        return extracted_contents
