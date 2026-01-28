from typing import Callable, Optional, Any
from core.types import ToolResult
import inspect


class ToolRegistry:
    """Tool 注册与执行系统"""
    
    def __init__(self):
        self._tools: dict[str, dict] = {}
    
    def register(self, name: str, description: str, parameters: dict):
        """
        装饰器: 注册一个 Tool
        
        使用示例:
        @registry.register(
            name="scheduler_add",
            description="添加定时提醒",
            parameters={
                "type": "object",
                "properties": {
                    "time": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["time", "content"]
            }
        )
        async def scheduler_add(time: str, content: str, context=None) -> str:
            # context 由 Engine 在调用时自动注入
            engine = context["engine"]
            await engine.send_push(...)
        """
        def decorator(func: Callable):
            # 检查函数签名，判断是否有 context 参数
            sig = inspect.signature(func)
            has_context = "context" in sig.parameters
            
            # 存储工具信息
            self._tools[name] = {
                "name": name,
                "description": description,
                "parameters": parameters,
                "func": func,
                "has_context": has_context,
                "is_async": inspect.iscoroutinefunction(func)
            }
            return func
        return decorator
    
    def get_schemas(self, names: list[str]) -> list[dict]:
        """
        输入: Tool 名称列表 ["scheduler_add", "scheduler_list"]
        输出: OpenAI Tool 格式的 schema 列表
        [
            {
                "type": "function",
                "function": {
                    "name": "scheduler_add",
                    "description": "添加定时提醒",
                    "parameters": {...}
                }
            }
        ]
        """
        schemas = []
        for name in names:
            if name not in self._tools:
                continue
            
            tool = self._tools[name]
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"]
                }
            })
        return schemas
    
    async def execute(self, name: str, args_dict: dict, context: dict = None) -> ToolResult:
        """
        执行 Tool（支持依赖注入）
        
        输入:
        - name: Tool 名称
        - args_dict: 参数字典（已解析的 JSON）
        - context: 注入的上下文 {"engine": ..., "scheduler": ..., "memory": ...}
        
        输出: ToolResult(success, output, error)
        
        流程:
        1. 检查 Tool 是否存在
        2. 检查 Tool 函数是否有 context 参数
        3. 如有，则注入 context
        4. 执行并返回结果
        """
        # 检查 Tool 是否存在
        if name not in self._tools:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{name}' not found"
            )
        
        tool = self._tools[name]
        func = tool["func"]
        has_context = tool["has_context"]
        is_async = tool["is_async"]
        
        try:
            # 准备参数
            if has_context and context is not None:
                # 注入 context
                args_dict = args_dict.copy()
                args_dict["context"] = context
            
            # 执行函数
            if is_async:
                result = await func(**args_dict)
            else:
                result = func(**args_dict)
            
            # 处理返回值
            if result is None:
                output = ""
            elif isinstance(result, str):
                output = result
            else:
                output = str(result)
            
            return ToolResult(
                success=True,
                output=output,
                error=None
            )
        
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=str(e)
            )
    
    def list_tools(self) -> list[str]:
        """返回所有已注册的 Tool 名称列表"""
        return list(self._tools.keys())


# 全局单例
registry = ToolRegistry()
