from typing import Callable, Optional, Any
from core.types import ToolResult
import inspect
import logging

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Tool 注册与执行系统（支持本地 Tool 和 MCP Tool）"""
    
    def __init__(self):
        self._tools: dict[str, dict] = {}
        self._mcp_manager = None  # 延迟初始化，避免循环导入
    
    def _get_mcp_manager(self):
        """获取 MCP 管理器（延迟导入）"""
        if self._mcp_manager is None:
            from tools.mcp_client import mcp_manager
            self._mcp_manager = mcp_manager
        return self._mcp_manager
    
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
    
    async def register_mcp_server(self, server):
        """
        注册 MCP Server，加载其工具
        
        参数:
        - server: MCPServer 实例
        
        工具名称格式: mcp:{server_name}:{tool_name}
        """
        mcp_manager = self._get_mcp_manager()
        
        success = await mcp_manager.register_server(server)
        if not success:
            logger.error(f"Failed to register MCP Server: {server.name}")
            return
        
        client = mcp_manager.get_client(server.name)
        if not client:
            return
        
        # 将 MCP 工具注册到本地 registry
        for tool in client.tools.values():
            tool_name = f"mcp:{server.name}:{tool.name}"
            self._tools[tool_name] = {
                "name": tool_name,
                "description": tool.description,
                "parameters": tool.input_schema,
                "mcp_server": server.name,
                "mcp_tool_name": tool.name,
                "is_mcp": True
            }
            logger.info(f"Registered MCP tool: {tool_name}")
    
    def get_schemas(self, names: list[str]) -> list[dict]:
        """
        输入: Tool 名称列表 ["scheduler_add", "scheduler_list", "mcp:filesystem:read_file"]
        输出: OpenAI Tool 格式的 schema 列表
        
        支持通配符:
        - "mcp:*" 匹配所有 MCP 工具
        - "mcp:filesystem:*" 匹配 filesystem server 的所有工具
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
        matched_names = set()
        
        for name in names:
            # 处理通配符
            if name.endswith(":*"):
                prefix = name[:-1]  # 去掉末尾的 *
                for tool_name in self._tools:
                    if tool_name.startswith(prefix) and tool_name not in matched_names:
                        matched_names.add(tool_name)
            elif name == "mcp:*":
                # 匹配所有 MCP 工具
                for tool_name in self._tools:
                    if tool_name.startswith("mcp:") and tool_name not in matched_names:
                        matched_names.add(tool_name)
            elif name in self._tools and name not in matched_names:
                matched_names.add(name)
        
        for name in matched_names:
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
        执行 Tool（支持依赖注入，支持 MCP 工具）
        
        输入:
        - name: Tool 名称（本地工具或 mcp:{server}:{tool} 格式）
        - args_dict: 参数字典（已解析的 JSON）
        - context: 注入的上下文 {"engine": ..., "scheduler": ..., "memory": ...}
        
        输出: ToolResult(success, output, error)
        
        流程:
        1. 检查是否是 MCP 工具
        2. 检查 Tool 是否存在
        3. 执行并返回结果
        """
        # 检查是否是 MCP 工具
        if name.startswith("mcp:"):
            return await self._execute_mcp_tool(name, args_dict)
        
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
    
    async def _execute_mcp_tool(self, name: str, args_dict: dict) -> ToolResult:
        """
        执行 MCP 工具
        
        参数:
        - name: mcp:{server}:{tool} 格式的工具名称
        - args_dict: 参数字典
        
        返回: ToolResult
        """
        from tools.mcp_client import MCPCallError
        
        # 解析 server 和 tool 名称
        parts = name.split(":", 2)
        if len(parts) != 3:
            return ToolResult(
                success=False,
                output="",
                error=f"Invalid MCP tool name format: {name}"
            )
        
        _, server_name, tool_name = parts
        
        # 获取 MCP 客户端
        mcp_manager = self._get_mcp_manager()
        client = mcp_manager.get_client(server_name)
        
        if not client:
            return ToolResult(
                success=False,
                output="",
                error=f"MCP Server '{server_name}' not found"
            )
        
        try:
            logger.info(f"Calling MCP tool: {name} with args: {args_dict}")
            result = await client.call_tool(tool_name, args_dict)
            logger.info(f"MCP tool {name} returned: {result[:200]}..." if len(str(result)) > 200 else f"MCP tool {name} returned: {result}")
            
            return ToolResult(
                success=True,
                output=str(result),
                error=None
            )
        
        except MCPCallError as e:
            logger.error(f"MCP tool {name} call error: {e}")
            return ToolResult(
                success=False,
                output="",
                error=str(e)
            )
        except Exception as e:
            logger.error(f"MCP tool {name} unexpected error: {e}")
            return ToolResult(
                success=False,
                output="",
                error=f"Unexpected error: {e}"
            )
    
    def list_tools(self, include_mcp: bool = True) -> list[str]:
        """
        返回所有已注册的 Tool 名称列表
        
        参数:
        - include_mcp: 是否包含 MCP 工具，默认 True
        
        返回: Tool 名称列表
        """
        if include_mcp:
            return list(self._tools.keys())
        else:
            return [name for name in self._tools.keys() if not name.startswith("mcp:")]
    
    def list_mcp_tools(self) -> list[str]:
        """返回所有 MCP 工具名称列表"""
        return [name for name in self._tools.keys() if name.startswith("mcp:")]
    
    async def shutdown_mcp(self):
        """关闭所有 MCP 连接"""
        mcp_manager = self._get_mcp_manager()
        await mcp_manager.disconnect_all()
        
        # 从 registry 中移除 MCP 工具
        mcp_tools = [name for name in self._tools if name.startswith("mcp:")]
        for name in mcp_tools:
            del self._tools[name]
        
        logger.info(f"Removed {len(mcp_tools)} MCP tools from registry")


# 全局单例
registry = ToolRegistry()
