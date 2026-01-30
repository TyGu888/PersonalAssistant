"""
Token 计数器（使用 tiktoken）

提供精确的 token 计数和文本截断功能，支持 OpenAI 及兼容 API。
"""

import tiktoken
import logging
from typing import Union

logger = logging.getLogger(__name__)


class TokenCounter:
    """Token 计数器（使用 tiktoken）"""
    
    def __init__(self, model: str = "gpt-4o"):
        """
        初始化 Token 计数器
        
        参数:
        - model: 模型名称，用于选择正确的 tokenizer
                 对于火山引擎等兼容 API 的模型名，会自动 fallback 到 cl100k_base
        """
        self.model = model
        
        # tiktoken 对于未知模型使用 cl100k_base
        try:
            self.encoding = tiktoken.encoding_for_model(model)
            logger.debug(f"使用模型 {model} 的 tokenizer")
        except KeyError:
            self.encoding = tiktoken.get_encoding("cl100k_base")
            logger.debug(f"模型 {model} 未知，使用 cl100k_base tokenizer")
    
    def count(self, text: str) -> int:
        """
        计算文本的 token 数
        
        参数:
        - text: 要计算的文本
        
        返回: token 数量
        """
        if not text:
            return 0
        return len(self.encoding.encode(text))
    
    def count_messages(self, messages: list[dict]) -> int:
        """
        计算消息列表的 token 数（OpenAI 格式）
        
        参数:
        - messages: OpenAI 格式的消息列表
                   [{"role": "...", "content": "..."}, ...]
        
        返回: 总 token 数
        
        说明:
        - 每条消息有固定开销（约 4 tokens: <|im_start|>{role}\n...content...<|im_end|>\n）
        - 对话开始有 3 tokens 的额外开销
        - 对于多模态消息（content 为列表），只计算文本部分
        """
        if not messages:
            return 0
        
        # 基础开销
        num_tokens = 3  # 对话开始的开销
        
        for message in messages:
            # 每条消息的固定开销
            num_tokens += 4
            
            # 计算 role
            role = message.get("role", "")
            num_tokens += self.count(role)
            
            # 计算 content
            content = message.get("content")
            if content is None:
                pass
            elif isinstance(content, str):
                num_tokens += self.count(content)
            elif isinstance(content, list):
                # 多模态消息，只计算文本部分
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        num_tokens += self.count(item.get("text", ""))
            
            # 如果有 name 字段
            if message.get("name"):
                num_tokens += self.count(message["name"])
                num_tokens += 1  # name 的额外开销
            
            # 如果有 tool_calls（function calling）
            tool_calls = message.get("tool_calls")
            if tool_calls:
                for tool_call in tool_calls:
                    if hasattr(tool_call, 'function'):
                        # OpenAI response 对象
                        num_tokens += self.count(tool_call.function.name or "")
                        num_tokens += self.count(tool_call.function.arguments or "")
                    elif isinstance(tool_call, dict):
                        # 字典格式
                        func = tool_call.get("function", {})
                        num_tokens += self.count(func.get("name", ""))
                        num_tokens += self.count(func.get("arguments", ""))
                    num_tokens += 3  # 每个 tool_call 的开销
        
        return num_tokens
    
    def truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """
        截断文本到指定 token 数
        
        参数:
        - text: 要截断的文本
        - max_tokens: 最大 token 数
        
        返回: 截断后的文本
        """
        if not text:
            return ""
        
        tokens = self.encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text
        
        truncated_tokens = tokens[:max_tokens]
        return self.encoding.decode(truncated_tokens)
    
    def truncate_messages_from_start(
        self, 
        messages: list[dict], 
        max_tokens: int,
        preserve_system: bool = True
    ) -> list[dict]:
        """
        从最早的消息开始截断，直到总 token 数符合要求
        
        参数:
        - messages: 消息列表
        - max_tokens: 最大 token 数
        - preserve_system: 是否保留 system 消息（默认 True）
        
        返回: 截断后的消息列表
        
        策略:
        1. 保留 system 消息
        2. 从最早的 user/assistant 消息开始删除
        3. 直到 token 数符合要求
        """
        if not messages:
            return []
        
        # 分离 system 消息和其他消息
        system_messages = []
        other_messages = []
        
        for msg in messages:
            if preserve_system and msg.get("role") == "system":
                system_messages.append(msg)
            else:
                other_messages.append(msg)
        
        # 计算 system 消息的 token 数
        system_tokens = self.count_messages(system_messages) if system_messages else 0
        
        # 可用于其他消息的 token 数
        available_tokens = max_tokens - system_tokens
        
        if available_tokens <= 0:
            logger.warning(f"System 消息已超过 max_tokens ({system_tokens} > {max_tokens})")
            return system_messages
        
        # 从后往前累计，直到超过可用 token 数
        kept_messages = []
        current_tokens = 3  # 基础开销
        
        for msg in reversed(other_messages):
            msg_tokens = self._count_single_message(msg)
            if current_tokens + msg_tokens <= available_tokens:
                kept_messages.insert(0, msg)
                current_tokens += msg_tokens
            else:
                # 超过限制，停止添加
                break
        
        result = system_messages + kept_messages
        
        # 记录截断信息
        removed_count = len(other_messages) - len(kept_messages)
        if removed_count > 0:
            final_tokens = self.count_messages(result)
            logger.info(
                f"上下文截断: 删除了 {removed_count} 条消息, "
                f"剩余 {len(result)} 条, {final_tokens} tokens"
            )
        
        return result
    
    def _count_single_message(self, message: dict) -> int:
        """计算单条消息的 token 数（不含基础开销）"""
        return self.count_messages([message]) - 3  # 减去基础开销
