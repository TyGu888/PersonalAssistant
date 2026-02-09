"""
CLI Client - 双模式命令行客户端

通过 WebSocket 连接到 Gateway，支持两种模式：
1. Chat 模式：类 Claude Code 风格的交互式对话
2. Tool Provider 模式：向 Agent 暴露本地工具（截图、打开浏览器等）

两种模式同时运行：用户可以聊天，Agent 也可以随时调用客户端工具。

使用方式：
    python main.py client
    python main.py client --host localhost --port 8080
    python main.py client --api-key your-key
"""

import asyncio
import json
import os
import logging
from typing import Optional, Callable

try:
    import websockets
except ImportError:
    websockets = None

logger = logging.getLogger(__name__)


# ===== ANSI Colors =====

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    BLUE = "\033[34m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"


def colored(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}"


# ===== Built-in Client Tools =====

# 客户端可以提供给 Agent 调用的本地工具。
# 每个工具是一个 dict: {"name", "description", "parameters", "handler"}
# handler 是 async def(arguments: dict) -> str

BUILTIN_CLIENT_TOOLS: list[dict] = []


def client_tool(name: str, description: str, parameters: dict):
    """装饰器：注册客户端本地工具"""
    def decorator(func: Callable):
        BUILTIN_CLIENT_TOOLS.append({
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": func,
        })
        return func
    return decorator


@client_tool(
    name="client_run_command",
    description="在客户端机器上执行 shell 命令并返回输出。用于需要在用户本地机器上执行操作的场景。",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令"
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数（默认 30）"
            }
        },
        "required": ["command"]
    }
)
async def client_run_command(arguments: dict) -> str:
    command = arguments.get("command", "")
    timeout = arguments.get("timeout", 30)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace")
        if stderr:
            output += "\n[stderr]\n" + stderr.decode(errors="replace")
        return output or "(no output)"
    except asyncio.TimeoutError:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


@client_tool(
    name="client_read_file",
    description="读取客户端机器上的文件内容",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径"
            }
        },
        "required": ["path"]
    }
)
async def client_read_file(arguments: dict) -> str:
    path = arguments.get("path", "")
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"Error: {e}"


@client_tool(
    name="client_list_files",
    description="列出客户端机器上指定目录的文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径（默认当前目录）"
            }
        },
        "required": []
    }
)
async def client_list_files(arguments: dict) -> str:
    path = arguments.get("path", ".")
    try:
        path = os.path.expanduser(path)
        entries = os.listdir(path)
        lines = []
        for entry in sorted(entries):
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                lines.append(f"  [dir]  {entry}/")
            else:
                size = os.path.getsize(full)
                lines.append(f"  [file] {entry} ({size} bytes)")
        return "\n".join(lines) or "(empty directory)"
    except Exception as e:
        return f"Error: {e}"


# ===== CLI Client =====

class CLIClient:
    """
    WebSocket CLI Client - 双模式
    
    1. Chat: 用户输入 → Agent 回复 (类似 Claude Code)
    2. Tool Provider: Agent 发送 tool_request → 客户端执行 → 返回 tool_result
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8080,
        api_key: Optional[str] = None,
        user_id: str = "cli_user",
        max_turns: int = 0,
    ):
        self.host = host
        self.port = port
        self.api_key = api_key
        self.user_id = user_id
        self.max_turns = max_turns  # 0 = unlimited
        self.ws = None
        self.running = False
        self.session_id = f"ws:dm:{user_id}"

        # 本地工具 handler 查找表
        self._tool_handlers: dict[str, Callable] = {
            t["name"]: t["handler"] for t in BUILTIN_CLIENT_TOOLS
        }
        
        # WS 读取锁：确保同一时刻只有一个协程在 recv()
        self._ws_lock = None  # asyncio.Lock, 在 run() 中初始化
        # 标记是否在聊天中（send_message 持有锁期间）
        self._chatting = False

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws"

    # ----- Connection -----

    async def connect(self) -> bool:
        """连接到 Gateway，认证，注册工具"""
        try:
            print(colored(f"Connecting to {self.ws_url}...", Colors.DIM))
            self.ws = await websockets.connect(self.ws_url)

            # Auth
            auth_msg = {"type": "auth"}
            if self.api_key:
                auth_msg["api_key"] = self.api_key
            await self.ws.send(json.dumps(auth_msg))
            response = json.loads(await self.ws.recv())

            if response.get("type") != "auth_ok":
                print(colored(f"Auth failed: {response.get('message', 'unknown')}", Colors.RED))
                return False

            print(colored("Connected!", Colors.GREEN))

            # Register client tools
            tool_schemas = [
                {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
                for t in BUILTIN_CLIENT_TOOLS
            ]
            if tool_schemas:
                await self.ws.send(json.dumps({"type": "register_tools", "tools": tool_schemas}))
                reg_response = json.loads(await self.ws.recv())
                if reg_response.get("type") == "tools_registered":
                    names = reg_response.get("names", [])
                    print(colored(f"Registered {len(names)} client tool(s): {', '.join(names)}", Colors.DIM))

            return True

        except Exception as e:
            print(colored(f"Connection failed: {e}", Colors.RED))
            return False

    # ----- Sending messages -----

    async def send_message(self, text: str) -> Optional[str]:
        """发送聊天消息，等待回复"""
        if not self.ws:
            return None

        msg = {
            "type": "message",
            "text": text,
            "user_id": self.user_id,
            "session_id": self.session_id,
        }
        
        # 获取 WS 读取锁，暂停 background listener
        async with self._ws_lock:
            self._chatting = True
            await self.ws.send(json.dumps(msg))

            try:
                # 等待回复 (可能先收到 tool_request，需要处理)
                while True:
                    raw = await self.ws.recv()
                    data = json.loads(raw)

                    if data.get("type") == "reply":
                        # 附件预览：若有 data/workspace 下的文件，打印可在浏览器打开的 URL
                        for att in data.get("attachments", []):
                            rel = None
                            if "data/workspace/" in att:
                                rel = att.split("data/workspace/", 1)[-1].lstrip("/").replace("\\", "/")
                            elif "data\\workspace\\" in att:
                                rel = att.split("data\\workspace\\", 1)[-1].lstrip("\\").replace("\\", "/")
                            if rel:
                                base = f"http://{self.host}:{self.port}"
                                print(colored(f"Preview: {base}/workspace/{rel}", Colors.CYAN))
                        return data.get("text", "")
                    elif data.get("type") == "error":
                        return colored(f"Error: {data.get('message', '')}", Colors.RED)
                    elif data.get("type") == "tool_request":
                        # Agent 请求执行客户端工具 (在等待回复期间)
                        await self._handle_tool_request(data)
                        continue
                    elif data.get("type") == "push":
                        # 推送消息，打印后继续等待回复
                        print(f"\n{colored('[Push]', Colors.YELLOW)} {data.get('text', '')}")
                        continue
                    else:
                        return str(data)
            finally:
                self._chatting = False

    # ----- Tool request handling -----

    async def _handle_tool_request(self, request: dict):
        """处理 Agent 的远程工具调用请求"""
        call_id = request.get("call_id", "")
        tool_name = request.get("tool_name", "")
        arguments = request.get("arguments", {})

        print(colored(f"\n[Tool Call] {tool_name}({json.dumps(arguments, ensure_ascii=False)[:100]})", Colors.MAGENTA))

        handler = self._tool_handlers.get(tool_name)
        if not handler:
            result_msg = {"type": "tool_result", "call_id": call_id, "error": f"Unknown tool: {tool_name}"}
        else:
            try:
                result = await handler(arguments)
                result_msg = {"type": "tool_result", "call_id": call_id, "result": result}
                # Show abbreviated result
                preview = result[:200] + "..." if len(result) > 200 else result
                print(colored(f"[Tool Result] {preview}", Colors.DIM))
            except Exception as e:
                result_msg = {"type": "tool_result", "call_id": call_id, "error": str(e)}
                print(colored(f"[Tool Error] {e}", Colors.RED))

        await self.ws.send(json.dumps(result_msg))

    # ----- Background listener (for pushes and tool requests outside chat) -----

    async def _background_listener(self):
        """后台监听 push 和 tool_request（仅在非 chatting 状态时读取 WS）"""
        try:
            while self.running and self.ws:
                try:
                    # 尝试获取锁（短超时），chatting 时 send_message 持有锁
                    # 用 wait_for 避免永久阻塞，让循环可以检查 self.running
                    try:
                        await asyncio.wait_for(self._ws_lock.acquire(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    
                    try:
                        raw = await asyncio.wait_for(self.ws.recv(), timeout=0.5)
                    finally:
                        self._ws_lock.release()
                    
                    data = json.loads(raw)

                    if data.get("type") == "push":
                        print(f"\n{colored('[Push]', Colors.YELLOW)} {data.get('text', '')}")
                        print(colored(f"{self.user_id}> ", Colors.BLUE), end="", flush=True)
                    elif data.get("type") == "tool_request":
                        await self._handle_tool_request(data)
                        print(colored(f"{self.user_id}> ", Colors.BLUE), end="", flush=True)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    logger.debug(f"Background listener error: {e}")
                    break
        except Exception:
            pass

    # ----- Main loop -----

    async def run(self):
        """主交互循环"""
        if not websockets:
            print(colored("Error: websockets package not installed.", Colors.RED))
            print("Install with: pip install websockets")
            return

        if not await self.connect():
            return

        self.running = True
        self._ws_lock = asyncio.Lock()

        # 启动后台监听（处理 push 和 tool_request，比如 Discord 触发的远程工具调用）
        bg_task = asyncio.create_task(self._background_listener())

        # 打印欢迎信息
        print()
        print(colored("=" * 50, Colors.CYAN))
        print(colored("  Personal Agent Hub - CLI Client", Colors.CYAN + Colors.BOLD))
        print(colored("=" * 50, Colors.CYAN))
        print(colored("  Type 'exit' or 'quit' to quit", Colors.DIM))
        print(colored("  Connected via WebSocket (chat + tool provider)", Colors.DIM))
        tools_count = len(BUILTIN_CLIENT_TOOLS)
        if tools_count:
            print(colored(f"  Providing {tools_count} local tool(s) to Agent", Colors.DIM))
        if self.max_turns > 0:
            print(colored(f"  Max turns: {self.max_turns}", Colors.DIM))
        print()

        loop = asyncio.get_event_loop()
        turn_count = 0

        while self.running:
            try:
                user_input = await loop.run_in_executor(
                    None, lambda: input(colored(f"{self.user_id}> ", Colors.BLUE))
                )

                if user_input.strip().lower() in ["exit", "quit", "/exit", "/quit"]:
                    print(colored("Goodbye!", Colors.GREEN))
                    break

                if not user_input.strip():
                    continue

                print(colored("Thinking...", Colors.DIM), end="\r")
                reply = await self.send_message(user_input.strip())

                # 清除 "Thinking..."
                print(" " * 40, end="\r")

                if reply:
                    print(colored("Assistant: ", Colors.GREEN + Colors.BOLD) + reply)
                print()

                # 检查 max_turns 限制
                turn_count += 1
                if self.max_turns > 0 and turn_count >= self.max_turns:
                    print(colored(f"Reached max turns ({self.max_turns}). Exiting.", Colors.YELLOW))
                    break

            except EOFError:
                print(colored("\nGoodbye!", Colors.GREEN))
                break
            except KeyboardInterrupt:
                print(colored("\n\nGoodbye!", Colors.GREEN))
                break
            except websockets.exceptions.ConnectionClosed:
                print(colored("\nConnection lost. Reconnecting...", Colors.YELLOW))
                if await self.connect():
                    # 重启 background listener（旧的已因 ConnectionClosed 退出）
                    bg_task.cancel()
                    try:
                        await bg_task
                    except asyncio.CancelledError:
                        pass
                    bg_task = asyncio.create_task(self._background_listener())
                    continue
                else:
                    print(colored("Reconnection failed.", Colors.RED))
                    break
            except Exception as e:
                print(colored(f"Error: {e}", Colors.RED))

        self.running = False
        bg_task.cancel()
        try:
            await bg_task
        except asyncio.CancelledError:
            pass
        if self.ws:
            await self.ws.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Personal Agent Hub CLI Client")
    parser.add_argument("--host", default="localhost", help="Gateway host")
    parser.add_argument("--port", type=int, default=8080, help="Gateway port")
    parser.add_argument("--api-key", default=None, help="API key for authentication")
    parser.add_argument("--user-id", default="cli_user", help="User ID")
    parser.add_argument("--max-turns", type=int, default=0, help="Max conversation turns (0 = unlimited)")

    args = parser.parse_args()

    client = CLIClient(
        host=args.host,
        port=args.port,
        api_key=args.api_key,
        user_id=args.user_id,
        max_turns=args.max_turns,
    )

    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
