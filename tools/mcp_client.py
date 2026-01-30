"""
MCP (Model Context Protocol) 客户端

通过 stdio 与 MCP Server 通信，支持工具调用
"""

import asyncio
import json
import logging
import os
from typing import Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MCPTool:
    """MCP 工具描述"""
    name: str
    description: str
    input_schema: dict  # JSON Schema


@dataclass
class MCPServer:
    """MCP Server 配置"""
    name: str
    command: str  # 启动命令，如 "npx"
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)


class MCPConnectionError(Exception):
    """MCP 连接错误"""
    pass


class MCPCallError(Exception):
    """MCP 调用错误"""
    pass


class MCPClient:
    """
    MCP 客户端 - 通过 stdio 与 MCP Server 通信
    
    实现 MCP 协议的基本握手（initialize/initialized）
    支持 tools/list 和 tools/call
    """
    
    # 重连配置
    MAX_RECONNECT_ATTEMPTS = 3
    RECONNECT_DELAY = 2  # 秒
    REQUEST_TIMEOUT = 30  # 秒
    
    def __init__(self, server: MCPServer):
        self.server = server
        self.process: Optional[asyncio.subprocess.Process] = None
        self.tools: dict[str, MCPTool] = {}
        self._request_id = 0
        self._connected = False
        self._lock = asyncio.Lock()  # 保护并发请求
    
    @property
    def connected(self) -> bool:
        """是否已连接"""
        return self._connected and self.process is not None and self.process.returncode is None
    
    async def connect(self) -> bool:
        """
        启动 MCP Server 进程并建立连接
        
        返回: 是否连接成功
        """
        async with self._lock:
            if self.connected:
                logger.debug(f"MCP Server {self.server.name} already connected")
                return True
            
            try:
                await self._do_connect()
                return True
            except Exception as e:
                logger.error(f"Failed to connect to MCP Server {self.server.name}: {e}")
                return False
    
    async def _do_connect(self):
        """执行实际的连接逻辑"""
        logger.info(f"Connecting to MCP Server: {self.server.name}")
        
        # 准备命令
        cmd = [self.server.command] + self.server.args
        logger.debug(f"MCP command: {' '.join(cmd)}")
        
        # 准备环境变量
        env = os.environ.copy()
        if self.server.env:
            for key, value in self.server.env.items():
                # 支持 ${VAR} 格式的环境变量引用
                if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                    env_var = value[2:-1]
                    env[key] = os.environ.get(env_var, "")
                else:
                    env[key] = value
        
        # 启动进程
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
        except FileNotFoundError as e:
            raise MCPConnectionError(f"Command not found: {cmd[0]}") from e
        except Exception as e:
            raise MCPConnectionError(f"Failed to start process: {e}") from e
        
        # 启动 stderr 监听任务
        asyncio.create_task(self._read_stderr(), name=f"mcp-stderr-{self.server.name}")
        
        # 发送 initialize 请求
        try:
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "PersonalAssistant", "version": "1.0"}
            })
            logger.debug(f"MCP {self.server.name} initialize result: {init_result}")
        except Exception as e:
            await self._cleanup_process()
            raise MCPConnectionError(f"Initialize handshake failed: {e}") from e
        
        # 发送 initialized 通知
        try:
            await self._send_notification("notifications/initialized", {})
        except Exception as e:
            await self._cleanup_process()
            raise MCPConnectionError(f"Initialized notification failed: {e}") from e
        
        # 获取可用工具
        try:
            result = await self._send_request("tools/list", {})
            self.tools.clear()
            for tool in result.get("tools", []):
                self.tools[tool["name"]] = MCPTool(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    input_schema=tool.get("inputSchema", {})
                )
            logger.info(f"MCP Server {self.server.name} connected, tools: {list(self.tools.keys())}")
        except Exception as e:
            await self._cleanup_process()
            raise MCPConnectionError(f"Failed to list tools: {e}") from e
        
        self._connected = True
    
    async def _read_stderr(self):
        """读取 stderr 日志"""
        if not self.process or not self.process.stderr:
            return
        
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                logger.debug(f"MCP {self.server.name} stderr: {line.decode().strip()}")
        except Exception as e:
            logger.debug(f"MCP {self.server.name} stderr reader error: {e}")
    
    async def _cleanup_process(self):
        """清理进程"""
        self._connected = False
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
            except Exception:
                pass
            self.process = None
    
    async def reconnect(self) -> bool:
        """
        重新连接 MCP Server
        
        返回: 是否重连成功
        """
        logger.info(f"Reconnecting to MCP Server: {self.server.name}")
        
        # 先断开
        await self.disconnect()
        
        # 尝试重连
        for attempt in range(1, self.MAX_RECONNECT_ATTEMPTS + 1):
            try:
                await self._do_connect()
                logger.info(f"MCP Server {self.server.name} reconnected on attempt {attempt}")
                return True
            except Exception as e:
                logger.warning(f"Reconnect attempt {attempt} failed: {e}")
                if attempt < self.MAX_RECONNECT_ATTEMPTS:
                    await asyncio.sleep(self.RECONNECT_DELAY * attempt)
        
        logger.error(f"Failed to reconnect to MCP Server {self.server.name} after {self.MAX_RECONNECT_ATTEMPTS} attempts")
        return False
    
    async def call_tool(self, name: str, arguments: dict) -> Any:
        """
        调用 MCP 工具
        
        参数:
        - name: 工具名称
        - arguments: 参数字典
        
        返回: 工具执行结果
        """
        # 检查连接状态
        if not self.connected:
            # 尝试重连
            if not await self.reconnect():
                raise MCPCallError(f"MCP Server {self.server.name} is not connected")
        
        try:
            result = await self._send_request("tools/call", {
                "name": name,
                "arguments": arguments
            })
            
            # 处理返回结果
            content = result.get("content", [])
            if isinstance(content, list):
                # 提取文本内容
                texts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            texts.append(item.get("text", ""))
                        elif item.get("type") == "image":
                            texts.append(f"[Image: {item.get('mimeType', 'image')}]")
                        elif item.get("type") == "resource":
                            texts.append(f"[Resource: {item.get('uri', '')}]")
                        else:
                            texts.append(str(item))
                    else:
                        texts.append(str(item))
                return "\n".join(texts) if texts else str(result)
            
            return str(result)
            
        except MCPCallError:
            raise
        except Exception as e:
            # 检查是否是连接问题，尝试重连
            if not self.connected:
                logger.warning(f"MCP {self.server.name} connection lost, attempting reconnect")
                if await self.reconnect():
                    # 重试一次
                    return await self.call_tool(name, arguments)
            raise MCPCallError(f"Tool call failed: {e}") from e
    
    async def _send_request(self, method: str, params: dict) -> dict:
        """
        发送 JSON-RPC 请求
        
        参数:
        - method: 方法名
        - params: 参数
        
        返回: 响应结果
        """
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise MCPConnectionError("Process not running")
        
        self._request_id += 1
        request_id = self._request_id
        
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params
        }
        
        logger.debug(f"MCP {self.server.name} request: {method} id={request_id}")
        
        # 写入 stdin
        line = json.dumps(request) + "\n"
        self.process.stdin.write(line.encode())
        await self.process.stdin.drain()
        
        # 读取 stdout 响应（带超时）
        try:
            response_line = await asyncio.wait_for(
                self.process.stdout.readline(),
                timeout=self.REQUEST_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise MCPCallError(f"Request timeout after {self.REQUEST_TIMEOUT}s")
        
        if not response_line:
            raise MCPConnectionError("Empty response from MCP Server")
        
        try:
            response = json.loads(response_line.decode())
        except json.JSONDecodeError as e:
            raise MCPCallError(f"Invalid JSON response: {e}")
        
        logger.debug(f"MCP {self.server.name} response: id={response.get('id')}")
        
        if "error" in response:
            error = response["error"]
            error_msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise MCPCallError(f"MCP Error: {error_msg}")
        
        return response.get("result", {})
    
    async def _send_notification(self, method: str, params: dict):
        """
        发送 JSON-RPC 通知（无 id，无响应）
        
        参数:
        - method: 方法名
        - params: 参数
        """
        if not self.process or not self.process.stdin:
            raise MCPConnectionError("Process not running")
        
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        
        logger.debug(f"MCP {self.server.name} notification: {method}")
        
        line = json.dumps(notification) + "\n"
        self.process.stdin.write(line.encode())
        await self.process.stdin.drain()
    
    async def disconnect(self):
        """关闭连接"""
        logger.info(f"Disconnecting from MCP Server: {self.server.name}")
        self._connected = False
        
        if self.process:
            try:
                # 优雅关闭
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    logger.warning(f"MCP Server {self.server.name} did not terminate gracefully, killing")
                    self.process.kill()
                    await self.process.wait()
            except Exception as e:
                logger.error(f"Error disconnecting MCP Server {self.server.name}: {e}")
            finally:
                self.process = None
        
        self.tools.clear()
        logger.info(f"MCP Server {self.server.name} disconnected")


class MCPManager:
    """
    MCP 管理器 - 管理多个 MCP Server 连接
    """
    
    def __init__(self):
        self._clients: dict[str, MCPClient] = {}
    
    async def register_server(self, server: MCPServer) -> bool:
        """
        注册并连接一个 MCP Server
        
        参数:
        - server: MCP Server 配置
        
        返回: 是否注册成功
        """
        if server.name in self._clients:
            logger.warning(f"MCP Server {server.name} already registered, skipping")
            return True
        
        client = MCPClient(server)
        
        try:
            success = await client.connect()
            if success:
                self._clients[server.name] = client
                logger.info(f"MCP Server {server.name} registered with {len(client.tools)} tools")
                return True
            else:
                logger.error(f"Failed to connect to MCP Server {server.name}")
                return False
        except Exception as e:
            logger.error(f"Error registering MCP Server {server.name}: {e}")
            return False
    
    def get_client(self, server_name: str) -> Optional[MCPClient]:
        """获取指定 server 的客户端"""
        return self._clients.get(server_name)
    
    def list_servers(self) -> list[str]:
        """列出所有已注册的 server 名称"""
        return list(self._clients.keys())
    
    def list_all_tools(self) -> dict[str, list[MCPTool]]:
        """
        列出所有 server 的工具
        
        返回: {server_name: [MCPTool, ...]}
        """
        result = {}
        for name, client in self._clients.items():
            result[name] = list(client.tools.values())
        return result
    
    async def disconnect_all(self):
        """断开所有 MCP Server 连接"""
        logger.info("Disconnecting all MCP Servers...")
        
        tasks = []
        for name, client in self._clients.items():
            tasks.append(client.disconnect())
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        self._clients.clear()
        logger.info("All MCP Servers disconnected")


# 全局单例
mcp_manager = MCPManager()
