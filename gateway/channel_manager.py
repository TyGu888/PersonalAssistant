"""
ChannelManager - Channel 生命周期管理

职责：
1. 根据配置创建和初始化 Channel 实例
2. 注入 MessageBus 到每个 Channel
3. 注册 Channel 的 deliver 函数到 Dispatcher
4. 管理 Channel 的启动、监控和重启（指数退避）
5. 优雅关闭所有 Channel
"""

import asyncio
import logging
from typing import Optional

from gateway.bus import MessageBus
from gateway.dispatcher import Dispatcher

logger = logging.getLogger(__name__)


class ChannelManager:
    """
    Channel 生命周期管理器
    
    负责创建、启动、监控和关闭所有 Channel。
    Channel 通过 MessageBus 与 Agent 通信，
    通过 Dispatcher 接收回复。
    """
    
    # 重启配置
    INITIAL_RESTART_DELAY = 5
    MAX_RESTART_DELAY = 300
    
    def __init__(self, bus: MessageBus, dispatcher: Dispatcher, config: dict):
        """
        参数:
        - bus: MessageBus 实例
        - dispatcher: Dispatcher 实例
        - config: 完整配置字典
        """
        self.bus = bus
        self.dispatcher = dispatcher
        self.config = config
        
        self.channels: dict = {}  # name -> BaseChannel
        self.contacts: dict = {}  # name -> contact info (lazy accumulated)
        self._channel_tasks: dict[str, asyncio.Task] = {}
        self._channel_restart_delays: dict[str, float] = {}
        self._shutdown_event = asyncio.Event()
    
    def init_channels(self, app=None):
        """根据配置初始化所有启用的 Channel。app: 可选 FastAPI 实例，WeCom 需用于注册回调路由"""
        channels_config = self.config.get("channels", {})
        
        # Telegram
        if channels_config.get("telegram", {}).get("enabled", False):
            from channels.telegram import TelegramChannel
            tg_config = channels_config["telegram"]
            channel = TelegramChannel(
                token=tg_config.get("token", ""),
                allowed_users=tg_config.get("allowed_users", []),
            )
            self._register_channel("telegram", channel)
        
        # Discord
        if channels_config.get("discord", {}).get("enabled", False):
            from channels.discord import DiscordChannel
            discord_config = channels_config["discord"]
            channel = DiscordChannel(
                token=discord_config.get("token", ""),
                allowed_users=discord_config.get("allowed_users", []),
            )
            self._register_channel("discord", channel)
        
        # Slack
        if channels_config.get("slack", {}).get("enabled", False):
            from channels.slack import SlackChannel
            slack_config = channels_config["slack"]
            channel = SlackChannel(
                bot_token=slack_config.get("bot_token", ""),
                app_token=slack_config.get("app_token", ""),
                allowed_users=slack_config.get("allowed_users", []),
            )
            self._register_channel("slack", channel)

        # Feishu (飞书)
        if channels_config.get("feishu", {}).get("enabled", False):
            from channels.feishu import FeishuChannel
            feishu_config = channels_config["feishu"]
            channel = FeishuChannel(
                app_id=feishu_config.get("app_id", ""),
                app_secret=feishu_config.get("app_secret", ""),
                encrypt_key=feishu_config.get("encrypt_key", ""),
                verification_token=feishu_config.get("verification_token", ""),
                allowed_users=feishu_config.get("allowed_users", []),
            )
            self._register_channel("feishu", channel)

        # QQ
        if channels_config.get("qq", {}).get("enabled", False):
            from channels.qq import QQChannel
            qq_config = channels_config["qq"]
            channel = QQChannel(
                appid=qq_config.get("appid", ""),
                secret=qq_config.get("secret", ""),
                allowed_users=qq_config.get("allowed_users", []),
            )
            self._register_channel("qq", channel)

        # WeCom (企业微信)
        if channels_config.get("wecom", {}).get("enabled", False):
            from channels.wecom import WeComChannel
            wecom_config = channels_config["wecom"]
            channel = WeComChannel(
                corp_id=wecom_config.get("corp_id", ""),
                app_secret=wecom_config.get("app_secret", ""),
                agent_id=str(wecom_config.get("agent_id", "")),
                token=wecom_config.get("token", ""),
                encoding_aes_key=wecom_config.get("encoding_aes_key", ""),
                allowed_users=wecom_config.get("allowed_users", []),
                app=app,
            )
            self._register_channel("wecom", channel)

        logger.info(f"ChannelManager initialized with channels: {list(self.channels.keys())}")
    
    def _register_channel(self, name: str, channel):
        """注册一个 Channel"""
        # 注入 MessageBus
        channel.set_bus(self.bus)
        
        # Inject contact callback for lazy accumulation
        channel.set_contact_callback(lambda info, cn=name: self.update_contacts(cn, info))
        
        # 注册到 Dispatcher（用于出站消息）
        self.dispatcher.register_channel(name, channel.deliver)
        
        # 保存引用
        self.channels[name] = channel
    
    def update_contacts(self, channel_name: str, info: dict):
        """Update contact registry for a channel (merge strategy)"""
        if channel_name not in self.contacts:
            self.contacts[channel_name] = {}
        existing = self.contacts[channel_name]
        # Deep merge: for each top-level key, if both are dicts, merge; else overwrite
        for key, value in info.items():
            if key in existing and isinstance(existing[key], dict) and isinstance(value, dict):
                existing[key].update(value)
            else:
                existing[key] = value
        logger.debug(f"Updated contacts for {channel_name}: {list(info.keys())}")
    
    def get_contacts_summary(self) -> dict:
        """Get the current contacts registry"""
        return dict(self.contacts)
    
    def remove_contact(self, channel_name: str, path: list) -> bool:
        """
        从通讯录中移除一条记录（按路径）。
        
        path: 交替的 key 与 id，如 ["guilds", "123"] 移除 guild 123，
              ["guilds", "123", "channels", "456"] 移除该 guild 下的 channel 456。
        返回: True 若移除成功，False 若路径不存在或无效。
        """
        if channel_name not in self.contacts or len(path) < 2 or len(path) % 2 != 0:
            return False
        parent = self.contacts[channel_name]
        for i in range(0, len(path) - 2, 2):
            k, v = path[i], path[i + 1]
            if k not in parent or v not in parent[k]:
                return False
            parent = parent[k][v]
        k, v = path[-2], path[-1]
        if k not in parent or v not in parent[k]:
            return False
        del parent[k][v]
        logger.info(f"Removed contact {channel_name} path={path}")
        return True
    
    def report_contacts(self, channel_name: str, info: dict):
        """Called by channels after startup scan to report initial contacts"""
        self.update_contacts(channel_name, info)
        logger.info(f"Channel '{channel_name}' reported contacts: {list(info.keys())}")
    
    async def start_all(self):
        """启动所有 Channel（带监控）"""
        for name in self.channels:
            self._channel_restart_delays[name] = self.INITIAL_RESTART_DELAY
            self._channel_tasks[name] = asyncio.create_task(
                self._monitor_channel(name),
                name=f"channel-monitor-{name}"
            )
        
        logger.info(f"Started monitoring {len(self._channel_tasks)} channel(s)")
    
    async def _monitor_channel(self, name: str):
        """监控单个 channel，崩溃时自动重启"""
        channel = self.channels[name]
        
        while not self._shutdown_event.is_set():
            try:
                logger.info(f"Starting channel: {name}")
                await channel.start()
                
                if self._shutdown_event.is_set():
                    break
                
                logger.warning(f"Channel {name} exited unexpectedly, will restart")
                
            except asyncio.CancelledError:
                logger.info(f"Channel {name} monitor cancelled")
                break
            except Exception as e:
                logger.error(f"Channel {name} crashed: {e}", exc_info=True)
            
            if self._shutdown_event.is_set():
                break
            
            # 指数退避重启
            delay = self._channel_restart_delays[name]
            logger.warning(f"Restarting channel {name} in {delay}s")
            
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=delay
                )
                break  # shutdown triggered
            except asyncio.TimeoutError:
                pass  # time to restart
            
            # 指数退避
            self._channel_restart_delays[name] = min(
                self._channel_restart_delays[name] * 2,
                self.MAX_RESTART_DELAY
            )
        
        logger.info(f"Channel {name} monitor exited")
    
    async def stop_all(self):
        """停止所有 Channel"""
        logger.info("Stopping all channels...")
        self._shutdown_event.set()
        
        # 停止所有 channel
        for name, channel in self.channels.items():
            try:
                logger.info(f"Stopping channel: {name}")
                await channel.stop()
            except Exception as e:
                logger.error(f"Error stopping channel {name}: {e}", exc_info=True)
        
        # 取消所有监控任务
        for task in self._channel_tasks.values():
            if not task.done():
                task.cancel()
        
        if self._channel_tasks:
            await asyncio.gather(*self._channel_tasks.values(), return_exceptions=True)
        
        # 注销所有 channel
        for name in self.channels:
            self.dispatcher.unregister_channel(name)
        
        logger.info("All channels stopped")
