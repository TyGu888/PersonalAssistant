from openai import AsyncOpenAI
from core.types import ChatMessage, ToolResult
from tools.registry import registry
from utils.token_counter import TokenCounter
import json
import logging
from typing import Optional, Union

logger = logging.getLogger(__name__)


class BaseAgent:
    def __init__(self, agent_id: str, system_prompt: str, llm_config: dict):
        """
        初始化 Agent
        
        参数:
        - agent_id: Agent 标识 (如 "study_coach", "default")
        - system_prompt: 系统提示词
        - llm_config: {"api_key": "...", "base_url": "...", "model": "...", "max_context_tokens": 8000}
        """
        self.agent_id = agent_id
        self.system_prompt = system_prompt
        self.model = llm_config.get("model", "gpt-4o")
        
        # Token 计数器和上下文限制
        self.token_counter = TokenCounter(self.model)
        self.max_context_tokens = llm_config.get("max_context_tokens", 8000)  # 留出响应空间
        self.max_response_tokens = llm_config.get("max_response_tokens")  # 可选：限制响应长度
        
        # 支持火山引擎等自定义 base_url
        client_kwargs = {"api_key": llm_config.get("api_key")}
        if llm_config.get("base_url"):
            client_kwargs["base_url"] = llm_config.get("base_url")
        self.client = AsyncOpenAI(**client_kwargs)
    
    async def run(
        self, 
        user_text: str, 
        context: dict, 
        tools: list[dict], 
        tool_context: dict = None,
        images: list[str] = None,
        msg_context: dict = None
    ) -> str:
        """
        运行 Agent 处理用户消息
        
        输入:
        - user_text: 用户消息
        - context: {
            "history": list[ChatMessage],  # 最近对话历史
            "memories": list[str]          # 相关长期记忆
          }
        - tools: Tool schema 列表（OpenAI function calling 格式）
        - tool_context: Tool 执行时注入的上下文 {"engine": ..., "scheduler": ..., "memory": ...}
        - images: 图片列表（路径或 data URL）
        - msg_context: 消息上下文（世界信息）{
            "user_id": str,           # 发消息的用户 ID
            "channel": str,           # 消息来源渠道
            "timestamp": datetime,    # 消息时间
            "is_group": bool,         # 是否群聊
            "group_id": str,          # 群 ID（群聊时）
            "is_owner": bool,         # 是否 owner
            "raw": dict               # 渠道特有信息
          }
        
        输出: str（回复文本）
        
        流程:
        1. 构建 messages:
           - system: prompt + memories + 世界信息
           - history: 最近对话
           - user: 当前消息（可能包含图片）
        2. 检查 token 数，必要时压缩上下文
        3. 调用 LLM
        4. 如果有 tool_calls:
           a. 执行 tools（通过 registry.execute）
           b. 将结果追加到 messages
           c. 重新调用 LLM
           d. 循环直到无 tool_calls 或达到最大循环次数
        5. 返回最终回复
        """
        # 构建系统消息
        memories = context.get("memories", [])
        system_content = self._build_system_message(memories, msg_context)
        
        # 构建消息列表
        history = context.get("history", [])
        messages = self._build_messages(system_content, history, user_text, images)
        
        # 检查并压缩上下文
        total_tokens = self.token_counter.count_messages(messages)
        if total_tokens > self.max_context_tokens:
            logger.info(
                f"上下文 token 数 ({total_tokens}) 超过限制 ({self.max_context_tokens})，开始压缩"
            )
            messages = self._compress_context(messages, self.max_context_tokens)
            compressed_tokens = self.token_counter.count_messages(messages)
            logger.info(f"上下文压缩完成: {total_tokens} -> {compressed_tokens} tokens")
        else:
            logger.debug(f"上下文 token 数: {total_tokens}/{self.max_context_tokens}")
        
        # 最大 tool_calls 循环次数
        max_iterations = 10
        iteration = 0
        
        # 构建 LLM 调用参数
        llm_kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": tools if tools else None,
            "tool_choice": "auto" if tools else None
        }
        if self.max_response_tokens:
            llm_kwargs["max_tokens"] = self.max_response_tokens
        
        while iteration < max_iterations:
            # 调用 LLM
            response = await self.client.chat.completions.create(**llm_kwargs)
            
            # 获取 assistant 消息
            assistant_message = response.choices[0].message
            messages.append({
                "role": "assistant",
                "content": assistant_message.content,
                "tool_calls": assistant_message.tool_calls
            })
            
            # 如果没有 tool_calls，返回最终回复
            if not assistant_message.tool_calls:
                return assistant_message.content or ""
            
            # 执行 tool calls
            tool_messages = await self._execute_tool_calls(
                assistant_message.tool_calls,
                tool_context
            )
            
            # 将 tool 结果追加到 messages
            messages.extend(tool_messages)
            
            iteration += 1
        
        # 如果达到最大循环次数，返回最后一次的回复
        return assistant_message.content or "已达到最大 tool_calls 循环次数，请重试。"
    
    def _build_system_message(self, memories: list[str], msg_context: dict = None) -> str:
        """
        构建系统消息（合并 prompt、世界信息和记忆）
        
        格式:
        {system_prompt}
        
        ## 当前消息上下文
        - 来源渠道: {channel}
        - 发送者: {user_id}
        - 时间: {timestamp}
        ...
        
        ## 关于用户的记忆
        - {memory1}
        - {memory2}
        ...
        
        ## 回复指南
        - 如果无需回复，输出 <NO_REPLY>
        """
        result = self.system_prompt
        
        # 添加世界信息（消息上下文）
        if msg_context:
            result += "\n\n## 当前消息上下文"
            result += f"\n- 来源渠道: {msg_context.get('channel', 'unknown')}"
            result += f"\n- 发送者 ID: {msg_context.get('user_id', 'unknown')}"
            
            timestamp = msg_context.get('timestamp')
            if timestamp:
                result += f"\n- 发送时间: {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            
            is_group = msg_context.get('is_group', False)
            result += f"\n- 是否群聊: {'是' if is_group else '否'}"
            
            if is_group and msg_context.get('group_id'):
                result += f"\n- 群 ID: {msg_context.get('group_id')}"
            
            # Owner 标识
            is_owner = msg_context.get('is_owner', False)
            if is_owner:
                result += "\n- **此消息来自 owner（主人），请认真对待每一条消息**"
            else:
                result += "\n- 此消息来自非 owner 用户，可酌情区分对待"
            
            # 渠道特有信息 (Raw Context)
            # 直接展示所有 raw 字段，供 LLM 使用（如 channel_id, message_id 等）
            raw = msg_context.get('raw', {})
            if raw:
                channel = msg_context.get('channel', 'unknown')
                result += f"\n\n### {channel.capitalize()} Context (Raw)"
                for k, v in raw.items():
                    result += f"\n- {k}: {v}"
        
        # 添加记忆
        if memories:
            result += "\n\n## 关于用户的记忆"
            for memory in memories:
                result += f"\n- {memory}"
        
        # 添加 NO_REPLY 机制说明
        result += "\n\n## 回复指南"
        result += "\n- 当你认为这条消息无需回复时（例如用户只是闲聊的一部分、感谢语、或消息不是针对你的），直接输出 `<NO_REPLY>` 作为完整回复"
        result += "\n- 如果需要正常回复，直接输出回复内容即可（不要包含 <NO_REPLY>）"
        
        return result
    
    def _build_messages(
        self, 
        system_content: str, 
        history: list[ChatMessage], 
        user_text: str,
        images: list[str] = None
    ) -> list[dict]:
        """
        构建 OpenAI messages 格式（支持多模态）
        
        输出: [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."},
            ...
            {"role": "user", "content": [...]}  # 可能包含图片
        ]
        
        图片消息格式 (OpenAI Vision):
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "描述这张图片"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
            ]
        }
        """
        messages = [
            {"role": "system", "content": system_content}
        ]
        
        # 添加历史消息
        for msg in history:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        # 添加当前用户消息（支持图片）
        user_message = self._build_user_message(user_text, images)
        messages.append(user_message)
        
        return messages
    
    def _build_user_message(self, user_text: str, images: list[str] = None) -> dict:
        """
        构建用户消息（支持图片）
        
        参数:
        - user_text: 用户文本
        - images: 图片列表，可以是:
            - 文件路径 (如 "/path/to/image.jpg")
            - data URL (如 "data:image/jpeg;base64,...")
        
        返回: OpenAI 消息格式
        """
        # 如果没有图片，返回纯文本格式
        if not images:
            return {
                "role": "user",
                "content": user_text
            }
        
        # 有图片，构建多模态消息
        content = []
        
        # 添加文本内容
        if user_text:
            content.append({
                "type": "text",
                "text": user_text
            })
        
        # 添加图片
        for image in images:
            image_url = self._process_image_for_message(image)
            if image_url:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": image_url}
                })
        
        # 如果最终没有有效内容，返回纯文本
        if not content:
            return {
                "role": "user",
                "content": user_text or ""
            }
        
        return {
            "role": "user",
            "content": content
        }
    
    def _process_image_for_message(self, image: str) -> Optional[str]:
        """
        处理图片，返回用于 LLM API 的 URL
        
        参数:
        - image: 图片路径或 data URL
        
        返回: data URL 或 None（处理失败时）
        """
        # 如果已经是 data URL，直接返回
        if image.startswith("data:"):
            return image
        
        # 如果是 HTTP URL，直接返回
        if image.startswith("http://") or image.startswith("https://"):
            return image
        
        # 否则认为是文件路径，处理并转换为 base64
        try:
            from tools.image import process_image_for_llm
            result = process_image_for_llm(image)
            return result["data_url"]
        except Exception as e:
            logger.error(f"处理图片失败 {image}: {str(e)}")
            return None
    
    def _compress_context(self, messages: list[dict], max_tokens: int) -> list[dict]:
        """
        压缩上下文到指定 token 数
        
        策略:
        1. 保留 system 消息
        2. 保留最后一条 user 消息（当前问题）
        3. 从最早的 user/assistant 消息开始删除
        4. 直到 token 数符合要求
        
        参数:
        - messages: 消息列表
        - max_tokens: 最大 token 数
        
        返回: 压缩后的消息列表
        """
        if not messages:
            return []
        
        # 分离消息
        system_messages = []
        history_messages = []
        last_user_message = None
        
        for i, msg in enumerate(messages):
            role = msg.get("role")
            if role == "system":
                system_messages.append(msg)
            elif i == len(messages) - 1 and role == "user":
                # 最后一条 user 消息（当前问题）
                last_user_message = msg
            else:
                history_messages.append(msg)
        
        # 计算必须保留的 token 数
        must_keep = system_messages + ([last_user_message] if last_user_message else [])
        must_keep_tokens = self.token_counter.count_messages(must_keep)
        
        # 可用于历史消息的 token 数
        available_tokens = max_tokens - must_keep_tokens
        
        if available_tokens <= 0:
            logger.warning(
                f"System + 当前消息已超过 max_tokens ({must_keep_tokens} > {max_tokens})，"
                f"移除所有历史"
            )
            return must_keep
        
        # 从后往前累计历史消息，直到超过可用 token 数
        kept_history = []
        current_tokens = 3  # 基础开销
        
        for msg in reversed(history_messages):
            msg_tokens = self.token_counter.count_messages([msg]) - 3  # 减去基础开销
            if current_tokens + msg_tokens <= available_tokens:
                kept_history.insert(0, msg)
                current_tokens += msg_tokens
            else:
                # 超过限制，停止添加
                break
        
        # 组合最终结果
        result = system_messages + kept_history
        if last_user_message:
            result.append(last_user_message)
        
        # 记录压缩信息
        removed_count = len(history_messages) - len(kept_history)
        if removed_count > 0:
            logger.info(f"上下文压缩: 删除了 {removed_count} 条历史消息")
        
        return result
    
    async def _execute_tool_calls(self, tool_calls, tool_context: dict) -> list[dict]:
        """
        执行 tool calls 并返回 tool messages
        
        输入: OpenAI response 中的 tool_calls
        输出: [{"role": "tool", "tool_call_id": "...", "content": "..."}]
        """
        tool_messages = []
        
        for tool_call in tool_calls:
            tool_call_id = tool_call.id
            tool_name = tool_call.function.name
            tool_args_str = tool_call.function.arguments
            
            # 解析 JSON 参数
            try:
                tool_args_dict = json.loads(tool_args_str)
            except json.JSONDecodeError as e:
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps({
                        "success": False,
                        "error": f"JSON 解析错误: {str(e)}"
                    })
                })
                continue
            
            # 执行 tool
            result = await registry.execute(tool_name, tool_args_dict, tool_context)
            
            # 格式化结果
            if result.success:
                content = result.output
            else:
                content = json.dumps({
                    "success": False,
                    "error": result.error or "未知错误"
                })
            
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content
            })
        
        return tool_messages
