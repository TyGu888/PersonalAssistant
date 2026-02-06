"""
CLI Client - 类 Claude Code 风格的命令行客户端

通过 WebSocket 连接到 Gateway，提供交互式对话界面。
支持：
- WebSocket 实时通信
- 自动重连
- 彩色输出
- 多行输入（以空行结束）

使用方式：
    python -m cli_client.client
    python -m cli_client.client --host localhost --port 8080
    python -m cli_client.client --api-key your-key
"""

import asyncio
import json
import sys
import signal
from typing import Optional

try:
    import websockets
except ImportError:
    websockets = None

import argparse


# ANSI 颜色
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


class CLIClient:
    """WebSocket CLI Client"""
    
    def __init__(self, host: str = "localhost", port: int = 8080, api_key: Optional[str] = None, user_id: str = "cli_user"):
        self.host = host
        self.port = port
        self.api_key = api_key
        self.user_id = user_id
        self.ws = None
        self.running = False
        self.session_id = f"ws:dm:{user_id}"
    
    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws"
    
    async def connect(self) -> bool:
        """连接到 Gateway 并认证"""
        try:
            print(colored(f"Connecting to {self.ws_url}...", Colors.DIM))
            self.ws = await websockets.connect(self.ws_url)
            
            # 认证
            auth_msg = {"type": "auth"}
            if self.api_key:
                auth_msg["api_key"] = self.api_key
            
            await self.ws.send(json.dumps(auth_msg))
            response = await self.ws.recv()
            data = json.loads(response)
            
            if data.get("type") == "auth_ok":
                print(colored("Connected!", Colors.GREEN))
                return True
            else:
                print(colored(f"Auth failed: {data.get('message', 'unknown')}", Colors.RED))
                return False
                
        except Exception as e:
            print(colored(f"Connection failed: {e}", Colors.RED))
            return False
    
    async def send_message(self, text: str) -> Optional[str]:
        """发送消息并等待回复"""
        if not self.ws:
            return None
        
        msg = {
            "type": "message",
            "text": text,
            "user_id": self.user_id,
            "session_id": self.session_id
        }
        
        await self.ws.send(json.dumps(msg))
        
        # 等待回复
        response = await self.ws.recv()
        data = json.loads(response)
        
        if data.get("type") == "reply":
            return data.get("text", "")
        elif data.get("type") == "error":
            return colored(f"Error: {data.get('message', '')}", Colors.RED)
        
        return str(data)
    
    async def listen_push(self):
        """后台监听 push 消息"""
        try:
            while self.running and self.ws:
                try:
                    msg = await asyncio.wait_for(self.ws.recv(), timeout=0.1)
                    data = json.loads(msg)
                    if data.get("type") == "push":
                        print(f"\n{colored('[Push]', Colors.YELLOW)} {data.get('text', '')}")
                        print(colored(f"\n{self.user_id}> ", Colors.BLUE), end="", flush=True)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
        except Exception:
            pass
    
    async def run(self):
        """主交互循环"""
        if not websockets:
            print(colored("Error: websockets package not installed.", Colors.RED))
            print("Install with: pip install websockets")
            return
        
        # 连接
        if not await self.connect():
            return
        
        self.running = True
        
        # 打印欢迎信息
        print()
        print(colored("=" * 40, Colors.CYAN))
        print(colored("  Personal Agent Hub - CLI Client", Colors.CYAN + Colors.BOLD))
        print(colored("=" * 40, Colors.CYAN))
        print(colored("  Type 'exit' or 'quit' to quit", Colors.DIM))
        print(colored("  Connected via WebSocket", Colors.DIM))
        print()
        
        loop = asyncio.get_event_loop()
        
        while self.running:
            try:
                # 读取输入
                user_input = await loop.run_in_executor(
                    None, lambda: input(colored(f"{self.user_id}> ", Colors.BLUE))
                )
                
                # 处理退出
                if user_input.strip().lower() in ['exit', 'quit', '/exit', '/quit']:
                    print(colored("Goodbye!", Colors.GREEN))
                    break
                
                # 跳过空输入
                if not user_input.strip():
                    continue
                
                # 发送并显示回复
                print(colored("Thinking...", Colors.DIM), end="\r")
                reply = await self.send_message(user_input.strip())
                
                # 清除 "Thinking..." 行
                print(" " * 30, end="\r")
                
                if reply:
                    print(colored("Assistant: ", Colors.GREEN + Colors.BOLD) + reply)
                print()
                
            except EOFError:
                print(colored("\nGoodbye!", Colors.GREEN))
                break
            except KeyboardInterrupt:
                print(colored("\n\nGoodbye!", Colors.GREEN))
                break
            except websockets.exceptions.ConnectionClosed:
                print(colored("\nConnection lost. Reconnecting...", Colors.YELLOW))
                if await self.connect():
                    continue
                else:
                    print(colored("Reconnection failed.", Colors.RED))
                    break
            except Exception as e:
                print(colored(f"Error: {e}", Colors.RED))
        
        self.running = False
        if self.ws:
            await self.ws.close()


def main():
    parser = argparse.ArgumentParser(description="Personal Agent Hub CLI Client")
    parser.add_argument("--host", default="localhost", help="Gateway host")
    parser.add_argument("--port", type=int, default=8080, help="Gateway port")
    parser.add_argument("--api-key", default=None, help="API key for authentication")
    parser.add_argument("--user-id", default="cli_user", help="User ID")
    
    args = parser.parse_args()
    
    client = CLIClient(
        host=args.host,
        port=args.port,
        api_key=args.api_key,
        user_id=args.user_id
    )
    
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
