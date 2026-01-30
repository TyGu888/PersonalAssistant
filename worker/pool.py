"""
Worker 进程池管理

支持:
- 固定数量的 Worker 进程
- 请求路由到空闲 Worker
- Worker 崩溃自动重启
- 优雅关闭
"""

import asyncio
import multiprocessing as mp
from multiprocessing.connection import Connection
from typing import Optional
import logging
import time

from worker.protocol import (
    AgentRequest,
    AgentResponse,
    SHUTDOWN_MESSAGE,
    HEALTH_CHECK_MESSAGE,
    HEALTH_OK_RESPONSE
)
from worker.agent_worker import start_worker

logger = logging.getLogger(__name__)


class WorkerInfo:
    """Worker 进程信息"""
    
    def __init__(self, worker_id: int, process: mp.Process, conn: Connection):
        self.worker_id = worker_id
        self.process = process
        self.conn = conn
        self.last_health_check: float = time.time()
        self.restart_count: int = 0


class WorkerPool:
    """
    Worker 进程池管理
    
    支持:
    - 固定数量的 Worker 进程
    - 请求路由到空闲 Worker
    - Worker 崩溃自动重启
    - 优雅关闭
    """
    
    # 配置常量
    MAX_RESTART_COUNT = 5       # 单个 Worker 最大重启次数
    RESTART_COOLDOWN = 60.0     # 重启冷却时间（秒），超过此时间重置重启计数
    HEALTH_CHECK_INTERVAL = 30  # 健康检查间隔（秒）
    RECV_TIMEOUT = 300          # 等待响应超时时间（秒）
    
    def __init__(self, config: dict, num_workers: int = 2):
        """
        初始化 Worker 池
        
        参数:
        - config: 配置字典（会传递给每个 Worker）
        - num_workers: Worker 进程数量
        """
        self.config = config
        self.num_workers = num_workers
        
        # Worker 管理
        self.workers: dict[int, WorkerInfo] = {}
        self.available_workers: asyncio.Queue = None
        
        # 状态
        self._started = False
        self._shutting_down = False
        self._monitor_task: Optional[asyncio.Task] = None
    
    async def start(self):
        """启动 Worker 池"""
        if self._started:
            logger.warning("WorkerPool already started")
            return
        
        logger.info(f"Starting WorkerPool with {self.num_workers} workers")
        
        self.available_workers = asyncio.Queue()
        
        # 启动所有 Worker
        for i in range(self.num_workers):
            await self._spawn_worker(i)
        
        # 启动监控任务
        self._monitor_task = asyncio.create_task(
            self._monitor_workers(),
            name="worker-pool-monitor"
        )
        
        self._started = True
        logger.info("WorkerPool started")
    
    async def _spawn_worker(self, worker_id: int) -> bool:
        """
        创建一个 Worker 进程
        
        返回: 是否成功
        """
        try:
            # 检查是否已存在
            if worker_id in self.workers:
                old_worker = self.workers[worker_id]
                if old_worker.process.is_alive():
                    logger.warning(f"Worker {worker_id} already running")
                    return False
                
                # 清理旧进程
                old_worker.conn.close()
            
            # 创建 Pipe
            parent_conn, child_conn = mp.Pipe()
            
            # 启动进程
            # 注意：使用 spawn 方法以避免共享状态问题
            ctx = mp.get_context('spawn')
            process = ctx.Process(
                target=start_worker,
                args=(self.config, child_conn, worker_id),
                name=f"agent-worker-{worker_id}"
            )
            process.start()
            
            # 关闭子进程端的连接（在父进程中不需要）
            child_conn.close()
            
            # 创建 WorkerInfo
            worker_info = WorkerInfo(worker_id, process, parent_conn)
            self.workers[worker_id] = worker_info
            
            # 添加到可用队列
            await self.available_workers.put(worker_id)
            
            logger.info(f"Worker {worker_id} spawned (PID: {process.pid})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to spawn worker {worker_id}: {e}", exc_info=True)
            return False
    
    async def _monitor_workers(self):
        """监控 Worker 健康状态，自动重启崩溃的 Worker"""
        while not self._shutting_down:
            try:
                await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)
                
                if self._shutting_down:
                    break
                
                for worker_id, worker_info in list(self.workers.items()):
                    if not worker_info.process.is_alive():
                        logger.warning(
                            f"Worker {worker_id} died (exit code: {worker_info.process.exitcode}), "
                            f"attempting restart..."
                        )
                        
                        # 检查重启次数
                        current_time = time.time()
                        if current_time - worker_info.last_health_check > self.RESTART_COOLDOWN:
                            # 重置重启计数
                            worker_info.restart_count = 0
                        
                        worker_info.restart_count += 1
                        
                        if worker_info.restart_count > self.MAX_RESTART_COUNT:
                            logger.error(
                                f"Worker {worker_id} exceeded max restart count, not restarting"
                            )
                            continue
                        
                        # 重启 Worker
                        await self._spawn_worker(worker_id)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in worker monitor: {e}", exc_info=True)
    
    async def submit(self, request: AgentRequest) -> AgentResponse:
        """
        提交请求到 Worker 池
        
        参数:
        - request: AgentRequest 请求
        
        返回: AgentResponse 响应
        
        异常:
        - RuntimeError: 如果 Worker 池未启动或已关闭
        - TimeoutError: 如果等待响应超时
        """
        if not self._started:
            raise RuntimeError("WorkerPool not started")
        
        if self._shutting_down:
            raise RuntimeError("WorkerPool is shutting down")
        
        # 获取空闲 Worker
        worker_id = await self.available_workers.get()
        worker_info = self.workers.get(worker_id)
        
        if not worker_info or not worker_info.process.is_alive():
            # Worker 已死，尝试重新获取
            logger.warning(f"Worker {worker_id} not available, trying another...")
            # 将此 Worker 标记为需要重启
            await self._spawn_worker(worker_id)
            worker_id = await self.available_workers.get()
            worker_info = self.workers.get(worker_id)
        
        try:
            # 发送请求
            worker_info.conn.send(request.to_json())
            
            # 等待响应（使用线程池避免阻塞事件循环）
            loop = asyncio.get_event_loop()
            
            # 带超时的等待
            try:
                response_data = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, 
                        self._recv_with_timeout, 
                        worker_info.conn,
                        self.RECV_TIMEOUT
                    ),
                    timeout=self.RECV_TIMEOUT + 5  # 额外 5 秒缓冲
                )
            except asyncio.TimeoutError:
                logger.error(f"Timeout waiting for response from worker {worker_id}")
                raise TimeoutError(f"Worker {worker_id} response timeout")
            
            if response_data is None:
                raise RuntimeError(f"Worker {worker_id} connection lost")
            
            return AgentResponse.from_json(response_data)
            
        finally:
            # 归还 Worker
            if not self._shutting_down:
                await self.available_workers.put(worker_id)
    
    def _recv_with_timeout(self, conn: Connection, timeout: float):
        """带超时的接收（在线程池中执行）"""
        if conn.poll(timeout=timeout):
            return conn.recv()
        return None
    
    async def shutdown(self):
        """关闭所有 Worker"""
        if not self._started:
            return
        
        logger.info("Shutting down WorkerPool...")
        self._shutting_down = True
        
        # 停止监控任务
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        # 向所有 Worker 发送关闭信号
        for worker_id, worker_info in self.workers.items():
            try:
                if worker_info.process.is_alive():
                    logger.info(f"Sending shutdown to worker {worker_id}")
                    worker_info.conn.send(SHUTDOWN_MESSAGE)
            except Exception as e:
                logger.error(f"Error sending shutdown to worker {worker_id}: {e}")
        
        # 等待所有 Worker 退出
        for worker_id, worker_info in self.workers.items():
            try:
                worker_info.process.join(timeout=5)
                if worker_info.process.is_alive():
                    logger.warning(f"Worker {worker_id} didn't exit gracefully, terminating...")
                    worker_info.process.terminate()
                    worker_info.process.join(timeout=2)
                    
                    if worker_info.process.is_alive():
                        logger.error(f"Worker {worker_id} still alive, killing...")
                        worker_info.process.kill()
            except Exception as e:
                logger.error(f"Error shutting down worker {worker_id}: {e}")
            finally:
                try:
                    worker_info.conn.close()
                except Exception:
                    pass
        
        self.workers.clear()
        self._started = False
        logger.info("WorkerPool shutdown complete")
    
    @property
    def worker_count(self) -> int:
        """返回活跃的 Worker 数量"""
        return sum(1 for w in self.workers.values() if w.process.is_alive())
    
    def get_status(self) -> dict:
        """获取 Worker 池状态"""
        return {
            "started": self._started,
            "shutting_down": self._shutting_down,
            "num_workers": self.num_workers,
            "active_workers": self.worker_count,
            "workers": {
                worker_id: {
                    "pid": info.process.pid,
                    "alive": info.process.is_alive(),
                    "restart_count": info.restart_count
                }
                for worker_id, info in self.workers.items()
            }
        }
