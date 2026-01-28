from openai import AsyncOpenAI
from core.types import ChatMessage, ToolResult
from tools.registry import registry
import json
from typing import Optional


class BaseAgent:
    def __init__(self, agent_id: str, system_prompt: str, llm_config: dict):
        """
        初始化 Agent
        
        参数:
        - agent_id: Agent 标识 (如 "study_coach", "default")
        - system_prompt: 系统提示词
        - llm_config: {"api_key": "...", "base_url": "...", "model": "..."}
        """
        self.agent_id = agent_id
        self.system_prompt = system_prompt
        self.model = llm_config.get("model", "gpt-4o")
        
        # 支持火山引擎等自定义 base_url
        client_kwargs = {"api_key": llm_config.get("api_key")}
        if llm_config.get("base_url"):
            client_kwargs["base_url"] = llm_config.get("base_url")
        self.client = AsyncOpenAI(**client_kwargs)
    
    async def run(self, user_text: str, context: dict, tools: list[dict], tool_context: dict = None) -> str:
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
        
        输出: str（回复文本）
        
        流程:
        1. 构建 messages:
           - system: prompt + memories
           - history: 最近对话
           - user: 当前消息
        2. 调用 LLM
        3. 如果有 tool_calls:
           a. 执行 tools（通过 registry.execute）
           b. 将结果追加到 messages
           c. 重新调用 LLM
           d. 循环直到无 tool_calls 或达到最大循环次数
        4. 返回最终回复
        """
        # 构建系统消息
        memories = context.get("memories", [])
        system_content = self._build_system_message(memories)
        
        # 构建消息列表
        history = context.get("history", [])
        messages = self._build_messages(system_content, history, user_text)
        
        # 最大 tool_calls 循环次数
        max_iterations = 10
        iteration = 0
        
        while iteration < max_iterations:
            # 调用 LLM
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None
            )
            
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
    
    def _build_system_message(self, memories: list[str]) -> str:
        """
        构建系统消息（合并 prompt 和记忆）
        
        格式:
        {system_prompt}
        
        ## 关于用户的记忆
        - {memory1}
        - {memory2}
        ...
        """
        if not memories:
            return self.system_prompt
        
        memory_section = "\n\n## 关于用户的记忆\n"
        for memory in memories:
            memory_section += f"- {memory}\n"
        
        return self.system_prompt + memory_section
    
    def _build_messages(self, system_content: str, history: list[ChatMessage], user_text: str) -> list[dict]:
        """
        构建 OpenAI messages 格式
        
        输出: [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."},
            ...
            {"role": "user", "content": user_text}
        ]
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
        
        # 添加当前用户消息
        messages.append({
            "role": "user",
            "content": user_text
        })
        
        return messages
    
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
