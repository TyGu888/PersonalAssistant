from channels.base import BaseChannel
from core.types import IncomingMessage, OutgoingMessage
import asyncio
from datetime import datetime


class CLIChannel(BaseChannel):
    """
    CLI Channel - 命令行交互界面
    
    用于 Phase 0 开发测试，在终端中进行一问一答式对话
    """
    
    def __init__(self, user_id: str = "cli_user"):
        """
        初始化 CLI Channel
        
        参数:
        - user_id: 模拟的用户 ID（默认 "cli_user"）
        """
        super().__init__()
        self.user_id = user_id
        self.running = False
    
    async def start(self):
        """
        启动 CLI 交互循环
        
        流程:
        1. 打印欢迎信息
        2. 循环读取用户输入
        3. 转换为 IncomingMessage
        4. 调用 on_message 处理
        5. 打印回复
        6. 输入 'exit' 或 'quit' 退出
        """
        self.running = True
        
        print("=" * 33)
        print(" Personal Agent Hub - CLI Mode")
        print("=" * 33)
        print("输入 'exit' 或 'quit' 退出")
        print()
        
        loop = asyncio.get_event_loop()
        
        while self.running:
            try:
                # 使用 run_in_executor 异步读取用户输入
                user_input = await loop.run_in_executor(None, input, "You: ")
                
                # 处理退出命令
                if user_input.strip().lower() in ['exit', 'quit', '/exit', '/quit']:
                    print("再见！")
                    self.running = False
                    break
                
                # 跳过空输入
                if not user_input.strip():
                    continue
                
                # 转换为 IncomingMessage
                incoming_msg = IncomingMessage(
                    channel="cli",
                    user_id=self.user_id,
                    text=user_input.strip(),
                    is_group=False,
                    group_id=None,
                    timestamp=datetime.utcnow(),
                    attachments=[],
                    raw={}
                )
                
                # 调用消息处理回调
                outgoing_msg = await self.publish_message(incoming_msg)
                
                # 打印回复
                print(f"Assistant: {outgoing_msg.text}")
                print()
                
            except EOFError:
                # 处理 Ctrl+D
                print("\n再见！")
                self.running = False
                break
            except KeyboardInterrupt:
                # 处理 Ctrl+C
                print("\n\n再见！")
                self.running = False
                break
            except Exception as e:
                print(f"错误: {e}")
                print()
    
    async def send(self, user_id: str, message: OutgoingMessage):
        """
        主动发送消息（在 CLI 中直接打印）
        
        格式: [推送] 消息内容
        """
        print(f"[推送] {message.text}")
        print()
    
    async def stop(self):
        """停止 CLI 交互"""
        self.running = False
