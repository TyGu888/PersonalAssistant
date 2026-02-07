import asyncio
import logging
from typing import Optional, Union

import discord

from channels.base import BaseChannel, ReconnectMixin
from core.types import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class DiscordChannel(BaseChannel, ReconnectMixin):
    """
    Discord Channel - Discord Bot 实现
    
    支持自动重连，使用指数退避策略
    """
    
    def __init__(
        self,
        token: str,
        allowed_users: list[str],
        max_retries: Optional[int] = None
    ):
        """
        初始化 Discord Channel
        
        参数:
        - token: Discord Bot Token
        - allowed_users: 允许的用户 ID 列表（白名单）
        - max_retries: 最大重试次数，None 表示无限重试
        """
        super().__init__()
        self.__init_reconnect__(max_retries)
        self.token = token
        self.allowed_users = set(allowed_users)  # 转为 set 提高查询效率
        self.client: Optional[discord.Client] = None
    
    def _create_client(self):
        """创建并配置 Discord Client"""
        # 设置 Intents（需要启用 message_content）
        intents = discord.Intents.default()
        intents.message_content = True
        
        # 创建 Discord Client
        self.client = discord.Client(intents=intents)
        
        # 注册消息事件处理器（使用闭包保持正确的函数名）
        @self.client.event
        async def on_ready():
            await self._on_ready()
        
        @self.client.event
        async def on_message(message):
            await self._on_message(message)
    
    async def start(self):
        """
        启动 Discord Bot（带重连循环）
        
        流程:
        1. 进入重连循环
        2. 创建 Client
        3. 使用 token 登录并启动
        4. 如果崩溃，等待后重试
        """
        self.is_running = True
        self._should_stop = False
        
        while not self._should_stop:
            try:
                # 每次重连时创建新的 client
                self._create_client()
                
                # 连接成功后重置重连状态（在 on_ready 中会确认连接成功）
                await self.client.start(self.token)
                
                # client.start() 返回意味着连接已断开
                logger.warning("Discord Bot disconnected")
                
                # 如果是正常停止，退出循环
                if self._should_stop:
                    break
                    
            except asyncio.CancelledError:
                logger.info("Discord Bot start cancelled")
                break
            except Exception as e:
                logger.error(f"Discord Bot connection error: {e}", exc_info=True)
                
                # 清理当前连接
                await self._cleanup()
                
                # 等待重连
                if not await self._wait_for_reconnect("DiscordChannel"):
                    logger.error("Discord Bot max retries reached or stopped, giving up")
                    break
        
        self.is_running = False
        logger.info("Discord Bot exited")
    
    async def _on_ready(self):
        """Bot 就绪时的回调"""
        # 连接成功，重置重连状态
        self._reset_reconnect_state()
        logger.info(f"Discord Bot logged in as {self.client.user}")
    
    async def _cleanup(self):
        """清理连接资源"""
        try:
            if self.client and not self.client.is_closed():
                await self.client.close()
        except Exception as e:
            logger.debug(f"Error closing client: {e}")
        finally:
            self.client = None
    
    async def _on_message(self, message: discord.Message):
        """
        Discord 消息处理
        
        流程:
        1. 过滤 bot 自己的消息
        2. 检查用户是否在白名单
        3. 判断是否群聊、是否被 @
        4. 转换为 IncomingMessage（设置 reply_expected）
        5. 调用 self.on_message（会记录消息到 memory）
        6. 如果需要回复，发送回复
        
        注意:
        - 群聊中所有消息都会记录到 session（供上下文使用）
        - 但只有被 @ 时才生成并发送回复
        - 私聊则正常流程（记录 + 回复）
        """
        try:
            # 过滤 bot 自己的消息
            if message.author.bot:
                return
            
            # 检查是否有消息内容
            if not message.content:
                return
            
            # 获取用户 ID
            user_id = str(message.author.id)
            
            # 判断是否群聊
            is_group = not isinstance(message.channel, discord.DMChannel)
            
            # 判断是否被 @ 提及
            is_mentioned = self.client.user in message.mentions
            
            # 检查白名单（群聊中即使未被 @ 也需要记录，所以仍需检查白名单）
            if user_id not in self.allowed_users:
                # 群聊未被 @ 时，静默忽略非白名单用户
                if is_group and not is_mentioned:
                    return
                # 被 @ 或私聊时，提示用户
                logger.warning(f"Unauthorized user {user_id} tried to send message")
                await message.channel.send("抱歉，您不在授权用户列表中。")
                return
            
            # 获取群组 ID（群聊时用 channel.id）
            group_id = str(message.channel.id) if is_group else None
            
            # 决定是否需要回复:
            # - 群聊中被 @ 才回复
            # - 私聊直接回复
            reply_expected = not is_group or is_mentioned
            
            # 构建 raw 字段，包含 Discord 特有信息
            raw = {
                "message_id": message.id,
                "channel_id": message.channel.id,
                "guild_id": message.guild.id if message.guild else None,
                "author_name": str(message.author),
                "author_display_name": message.author.display_name,
            }
            
            # 添加 channel name
            if hasattr(message.channel, 'name'):
                raw["channel_name"] = message.channel.name
            
            # 添加 guild name
            if message.guild:
                raw["guild_name"] = message.guild.name
            
            # 添加 thread 信息（如果在 thread 中）
            if isinstance(message.channel, discord.Thread):
                raw["is_thread"] = True
                raw["thread_id"] = message.channel.id
                raw["thread_name"] = message.channel.name
                raw["parent_channel_id"] = message.channel.parent_id
                if message.channel.parent:
                    raw["parent_channel_name"] = message.channel.parent.name
            else:
                raw["is_thread"] = False
            
            # 构建 IncomingMessage
            incoming_message = IncomingMessage(
                channel="discord",
                user_id=user_id,
                text=message.content,
                is_group=is_group,
                group_id=group_id,
                reply_expected=reply_expected,
                raw=raw
            )
            
            # 调用消息处理回调
            if reply_expected:
                logger.info(f"Received message from user {user_id}: {message.content[:50]}...")
            else:
                logger.debug(f"Recording group message from user {user_id} (no reply expected)")
            
            # publish to bus (fire-and-forget, Dispatcher handles reply delivery)
            await self.publish_message(incoming_message)
            
        except Exception as e:
            logger.error(f"Error handling Discord message: {e}", exc_info=True)
            # 尝试发送错误提示（只有需要回复时才发送错误）
            try:
                is_group = not isinstance(message.channel, discord.DMChannel)
                is_mentioned = self.client.user in message.mentions
                if message.channel and (not is_group or is_mentioned):
                    await message.channel.send("处理消息时发生错误，请稍后重试。")
            except:
                pass
    
    async def _send_message(self, channel: Union[discord.TextChannel, discord.DMChannel], text: str, attachments: list = None):
        """
        发送消息，处理 Discord 的 2000 字符限制，支持文件附件
        
        参数:
        - channel: Discord 频道
        - text: 消息文本
        - attachments: 文件路径列表
        """
        import os
        
        DISCORD_MAX_LENGTH = 2000
        
        # 准备文件附件
        files = []
        if attachments:
            for file_path in attachments:
                if os.path.exists(file_path):
                    files.append(discord.File(file_path))
                else:
                    logger.warning(f"Attachment file not found: {file_path}")
        
        # 如果有文件，第一条消息带上文件
        if len(text) <= DISCORD_MAX_LENGTH:
            await channel.send(text or None, files=files if files else None)
        else:
            # 分片发送
            chunks = []
            current_chunk = ""
            
            # 按行分割，尽量保持完整性
            lines = text.split('\n')
            for line in lines:
                # 如果当前行本身就超过限制，需要进一步分割
                if len(line) > DISCORD_MAX_LENGTH:
                    # 先发送当前累积的 chunk
                    if current_chunk:
                        chunks.append(current_chunk)
                        current_chunk = ""
                    
                    # 将超长行按字符分割
                    for i in range(0, len(line), DISCORD_MAX_LENGTH):
                        chunks.append(line[i:i + DISCORD_MAX_LENGTH])
                else:
                    # 检查加上这一行是否会超过限制
                    if len(current_chunk) + len(line) + 1 > DISCORD_MAX_LENGTH:
                        # 发送当前 chunk
                        if current_chunk:
                            chunks.append(current_chunk)
                            current_chunk = line
                        else:
                            # 当前行单独发送
                            chunks.append(line)
                            current_chunk = ""
                    else:
                        # 添加到当前 chunk
                        if current_chunk:
                            current_chunk += '\n' + line
                        else:
                            current_chunk = line
            
            # 发送最后一个 chunk
            if current_chunk:
                chunks.append(current_chunk)
            
            # 发送所有 chunks（第一个 chunk 带文件）
            for i, chunk in enumerate(chunks):
                if i == 0 and files:
                    await channel.send(chunk, files=files)
                else:
                    await channel.send(chunk)
    
    async def deliver(self, target: dict, message: OutgoingMessage):
        """
        投递消息到 Discord 目标
        
        target 字段:
        - channel_id: Discord 频道/线程 ID (优先使用)
        - user_id: 用户 ID (DM 回退)
        """
        try:
            if not self.client or not self.client.is_ready():
                logger.error("Discord client not ready, cannot deliver message")
                return
            
            if not message or not message.text:
                logger.warning("Empty message, skipping deliver")
                return
            
            channel_id = target.get("channel_id")
            user_id = target.get("user_id")
            
            # 优先使用 channel_id (回复到原始频道/线程/DM)
            if channel_id:
                ch = self.client.get_channel(int(channel_id))
                if not ch:
                    try:
                        ch = await self.client.fetch_channel(int(channel_id))
                    except Exception as e:
                        logger.error(f"Failed to fetch channel {channel_id}: {e}")
                        ch = None
                if ch:
                    await self._send_message(ch, message.text, message.attachments)
                    logger.info(f"Delivered message to Discord channel {channel_id}")
                    return
            
            # 回退: DM 发送
            if user_id:
                if str(user_id) not in self.allowed_users:
                    logger.warning(f"Cannot deliver to unauthorized user {user_id}")
                    return
                try:
                    user = await self.client.fetch_user(int(user_id))
                    dm = await user.create_dm()
                    await self._send_message(dm, message.text, message.attachments)
                    logger.info(f"Delivered DM to user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to deliver DM to {user_id}: {e}")
            else:
                logger.warning(f"No valid target for Discord delivery: {target}")
                
        except Exception as e:
            logger.error(f"Error delivering Discord message: {e}", exc_info=True)
    
    async def stop(self):
        """停止 Bot（会退出重连循环）"""
        logger.info("Stopping Discord Bot...")
        self._should_stop = True
        
        try:
            await self._cleanup()
            logger.info("Discord Bot stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping Discord Bot: {e}", exc_info=True)
            raise
        finally:
            self.is_running = False
