import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.ext import Application, MessageHandler as TGMessageHandler, filters, ContextTypes

from channels.base import BaseChannel, ReconnectMixin
from core.types import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel, ReconnectMixin):
    """
    Telegram Channel - Telegram Bot 实现
    
    支持自动重连，使用指数退避策略
    """
    
    def __init__(
        self,
        token: str,
        allowed_users: list[str],
        max_retries: Optional[int] = None
    ):
        """
        初始化 Telegram Channel
        
        参数:
        - token: Telegram Bot Token
        - allowed_users: 允许的用户 ID 列表（白名单）
        - max_retries: 最大重试次数，None 表示无限重试
        """
        super().__init__()
        self.__init_reconnect__(max_retries)
        self.token = token
        self.allowed_users = set(allowed_users)  # 转为 set 提高查询效率
        self.application: Optional[Application] = None
    
    async def start(self):
        """
        启动 Telegram Bot（带重连循环）
        
        流程:
        1. 进入重连循环
        2. 创建 Application
        3. 注册 message handler
        4. 启动 polling
        5. 如果崩溃，等待后重试
        """
        self.is_running = True
        self._should_stop = False
        
        while not self._should_stop:
            try:
                await self._connect()
                # 连接成功后重置重连状态
                self._reset_reconnect_state()
                logger.info("Telegram Bot started successfully")
                
                # 保持运行直到停止或出错
                # polling 会在内部保持运行
                while not self._should_stop and self.application:
                    await asyncio.sleep(1)
                
                # 如果是正常停止，退出循环
                if self._should_stop:
                    break
                    
            except asyncio.CancelledError:
                logger.info("Telegram Bot start cancelled")
                break
            except Exception as e:
                logger.error(f"Telegram Bot connection error: {e}", exc_info=True)
                
                # 清理当前连接
                await self._cleanup()
                
                # 等待重连
                if not await self._wait_for_reconnect("TelegramChannel"):
                    logger.error("Telegram Bot max retries reached or stopped, giving up")
                    break
        
        self.is_running = False
        logger.info("Telegram Bot exited")
    
    async def _connect(self):
        """
        建立连接
        
        流程:
        1. 创建 Application
        2. 注册 message handler
        3. 启动 polling
        """
        # 创建 Application
        self.application = Application.builder().token(self.token).build()
        
        # 注册消息处理器（处理文本消息，排除命令）
        self.application.add_handler(
            TGMessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_telegram_message)
        )
        
        # 初始化并启动 polling
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
    
    async def _cleanup(self):
        """清理连接资源"""
        try:
            if self.application:
                try:
                    if self.application.updater and self.application.updater.running:
                        await self.application.updater.stop()
                except Exception as e:
                    logger.debug(f"Error stopping updater: {e}")
                
                try:
                    if self.application.running:
                        await self.application.stop()
                except Exception as e:
                    logger.debug(f"Error stopping application: {e}")
                
                try:
                    await self.application.shutdown()
                except Exception as e:
                    logger.debug(f"Error shutting down application: {e}")
                
                self.application = None
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)
    
    async def _handle_telegram_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Telegram 消息处理
        
        流程:
        1. 检查用户是否在白名单
        2. 转换为 IncomingMessage
        3. 调用 self.on_message
        4. 发送回复
        """
        try:
            # 检查是否有消息
            if not update.message or not update.message.text:
                return
            
            # 获取用户 ID
            user_id = str(update.effective_user.id)
            
            # 检查白名单
            if user_id not in self.allowed_users:
                logger.warning(f"Unauthorized user {user_id} tried to send message")
                await update.message.reply_text("抱歉，您不在授权用户列表中。")
                return
            
            # 判断是否群聊
            chat_type = update.effective_chat.type
            is_group = chat_type in ['group', 'supergroup']
            group_id = str(update.effective_chat.id) if is_group else None
            
            # 构建 IncomingMessage
            incoming_message = IncomingMessage(
                channel="telegram",
                user_id=user_id,
                text=update.message.text,
                is_group=is_group,
                group_id=group_id,
                raw={
                    "update_id": update.update_id,
                    "message_id": update.message.message_id,
                    "chat_id": update.effective_chat.id,
                    "chat_type": chat_type,
                }
            )
            
            # 发布到 MessageBus（fire-and-forget，Dispatcher 会通过 deliver() 路由回复）
            logger.info(f"Received message from user {user_id}: {update.message.text[:50]}...")
            await self.publish_message(incoming_message)
            
        except Exception as e:
            logger.error(f"Error handling Telegram message: {e}", exc_info=True)
            # 尝试发送错误提示
            try:
                if update.message:
                    await update.message.reply_text("处理消息时发生错误，请稍后重试。")
            except:
                pass
    
    async def deliver(self, target: dict, message: OutgoingMessage):
        """
        投递消息到 Telegram 目标
        
        target 字段:
        - chat_id: Telegram chat ID (优先使用, 适用于私聊和群聊)
        - user_id: 用户 ID (DM 回退)
        """
        try:
            if not self.application:
                logger.error("Telegram application not initialized, cannot deliver")
                return
            
            if not message or not message.text:
                logger.warning("Empty message, skipping deliver")
                return
            
            chat_id = target.get("chat_id")
            user_id = target.get("user_id")
            
            # 优先使用 chat_id (回复到原始会话)
            target_id = chat_id or user_id
            if not target_id:
                logger.warning(f"No valid target for Telegram delivery: {target}")
                return
            
            await self.application.bot.send_message(
                chat_id=int(target_id),
                text=message.text
            )
            logger.info(f"Delivered Telegram message to chat {target_id}")
            
        except Exception as e:
            logger.error(f"Error delivering Telegram message: {e}", exc_info=True)
    
    async def stop(self):
        """停止 Bot（会退出重连循环）"""
        logger.info("Stopping Telegram Bot...")
        self._should_stop = True
        
        try:
            await self._cleanup()
            logger.info("Telegram Bot stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping Telegram Bot: {e}", exc_info=True)
            raise
        finally:
            self.is_running = False
