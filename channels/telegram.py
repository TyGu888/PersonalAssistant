from channels.base import BaseChannel, MessageHandler
from core.types import IncomingMessage, OutgoingMessage
from telegram import Update
from telegram.ext import Application, MessageHandler as TGMessageHandler, filters, ContextTypes
from typing import Optional
import logging

logger = logging.getLogger(__name__)

class TelegramChannel(BaseChannel):
    """
    Telegram Channel - Telegram Bot 实现
    """
    
    def __init__(self, token: str, allowed_users: list[str], on_message: MessageHandler):
        """
        初始化 Telegram Channel
        
        参数:
        - token: Telegram Bot Token
        - allowed_users: 允许的用户 ID 列表（白名单）
        - on_message: 消息处理回调
        """
        super().__init__(on_message)
        self.token = token
        self.allowed_users = set(allowed_users)  # 转为 set 提高查询效率
        self.application: Optional[Application] = None
    
    async def start(self):
        """
        启动 Telegram Bot
        
        流程:
        1. 创建 Application
        2. 注册 message handler
        3. 启动 polling
        """
        try:
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
            
            logger.info("Telegram Bot started successfully")
        except Exception as e:
            logger.error(f"Failed to start Telegram Bot: {e}", exc_info=True)
            raise
    
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
            
            # 调用消息处理回调
            logger.info(f"Received message from user {user_id}: {update.message.text[:50]}...")
            outgoing_message = await self.on_message(incoming_message)
            
            # 发送回复
            if outgoing_message and outgoing_message.text:
                await update.message.reply_text(outgoing_message.text)
                logger.info(f"Sent reply to user {user_id}")
            
        except Exception as e:
            logger.error(f"Error handling Telegram message: {e}", exc_info=True)
            # 尝试发送错误提示
            try:
                if update.message:
                    await update.message.reply_text("处理消息时发生错误，请稍后重试。")
            except:
                pass
    
    async def send(self, user_id: str, message: OutgoingMessage):
        """
        主动发送消息
        
        用于定时提醒等主动推送场景
        """
        try:
            if not self.application:
                logger.error("Application not initialized, cannot send message")
                return
            
            if not message or not message.text:
                logger.warning("Empty message, skipping send")
                return
            
            # 检查用户是否在白名单
            if user_id not in self.allowed_users:
                logger.warning(f"Cannot send message to unauthorized user {user_id}")
                return
            
            # 发送消息
            await self.application.bot.send_message(
                chat_id=user_id,
                text=message.text
            )
            logger.info(f"Sent proactive message to user {user_id}")
            
        except Exception as e:
            logger.error(f"Error sending message to user {user_id}: {e}", exc_info=True)
            raise
    
    async def stop(self):
        """停止 Bot"""
        try:
            if self.application:
                # 停止 polling
                await self.application.updater.stop()
                # 停止 application
                await self.application.stop()
                # 关闭 application
                await self.application.shutdown()
                logger.info("Telegram Bot stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping Telegram Bot: {e}", exc_info=True)
            raise
