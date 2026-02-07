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
        self._channel_tasks: dict[str, asyncio.Task] = {}
        self._channel_restart_delays: dict[str, float] = {}
        self._shutdown_event = asyncio.Event()
    
    def init_channels(self):
        """根据配置初始化所有启用的 Channel"""
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
        
        logger.info(f"ChannelManager initialized with channels: {list(self.channels.keys())}")
    
    def _register_channel(self, name: str, channel):
        """注册一个 Channel"""
        # 注入 MessageBus
        channel.set_bus(self.bus)
        
        # 注册到 Dispatcher（用于出站消息）
        self.dispatcher.register_channel(name, channel.deliver)
        
        # 保存引用
        self.channels[name] = channel
    
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
