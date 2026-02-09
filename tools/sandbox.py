"""
Docker 沙箱模块 - 提供容器隔离执行环境（纯基础设施，无工具注册）

功能：
- 创建隔离的 Docker 容器执行命令
- 支持热容器复用（不用每次都创建新容器）
- 资源限制（内存、CPU）
- 超时保护
- 文件复制（双向）

注意：
- 所有工具注册已移至 tools/shell.py
- 本模块仅提供 DockerSandbox 类和辅助函数
- shell.py 通过 get_sandbox() / is_sandbox_enabled() 使用本模块
"""

import docker
import asyncio
import tarfile
import io
import os
from typing import Optional, Tuple
from pathlib import Path


class DockerSandbox:
    """Docker 容器沙箱"""
    
    def __init__(
        self,
        image: str = "personalassistant-sandbox:latest",
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        network_mode: str = "none",
        workspace_mount: Optional[str] = None
    ):
        """
        初始化沙箱配置
        
        Args:
            image: Docker 镜像名称
            memory_limit: 内存限制 (例如 "512m", "1g")
            cpu_limit: CPU 限制 (例如 1.0 表示 1 核)
            network_mode: 网络模式 ("none" 表示无网络, "bridge" 表示桥接)
            workspace_mount: 宿主机工作目录挂载路径
        """
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.network_mode = network_mode
        self.workspace_mount = workspace_mount
        
        self.client: Optional[docker.DockerClient] = None
        self.container: Optional[docker.models.containers.Container] = None
        self._is_running = False
    
    def _ensure_client(self):
        """确保 Docker 客户端已初始化"""
        if self.client is None:
            try:
                self.client = docker.from_env()
                # 测试连接
                self.client.ping()
            except docker.errors.DockerException as e:
                raise RuntimeError(f"无法连接到 Docker: {e}")
    
    async def start(self, workspace_dir: Optional[str] = None) -> str:
        """
        启动沙箱容器
        
        Args:
            workspace_dir: 可选的工作目录挂载路径（覆盖初始化时的配置）
        
        Returns:
            容器 ID
        """
        self._ensure_client()
        
        # 如果容器已经在运行，直接返回
        if self._is_running and self.container:
            try:
                self.container.reload()
                if self.container.status == "running":
                    return self.container.id
            except docker.errors.NotFound:
                self._is_running = False
                self.container = None
        
        # 使用传入的 workspace_dir 或初始化时的配置
        mount_dir = workspace_dir or self.workspace_mount
        
        # 准备容器配置
        container_config = {
            "image": self.image,
            "command": ["sleep", "infinity"],
            "detach": True,
            "auto_remove": False,
            "user": "sandbox",
            "working_dir": "/workspace",
            # 安全配置
            "read_only": False,  # 允许写入 /workspace
            "cap_drop": ["ALL"],  # 移除所有 capabilities
            "security_opt": ["no-new-privileges:true"],
            # 资源限制
            "mem_limit": self.memory_limit,
            "nano_cpus": int(self.cpu_limit * 1e9),
            # 网络配置
            "network_mode": self.network_mode,
        }
        
        # 配置挂载
        if mount_dir:
            mount_path = Path(mount_dir).resolve()
            if mount_path.exists():
                container_config["volumes"] = {
                    str(mount_path): {
                        "bind": "/workspace",
                        "mode": "rw"
                    }
                }
        
        # 创建临时目录挂载（如果没有指定工作目录）
        if "volumes" not in container_config:
            container_config["tmpfs"] = {
                "/workspace": "size=100M,uid=1000,gid=1000"
            }
        
        try:
            # 确保镜像存在
            try:
                self.client.images.get(self.image)
            except docker.errors.ImageNotFound:
                # 尝试拉取镜像
                print(f"镜像 {self.image} 不存在，尝试拉取...")
                try:
                    self.client.images.pull(self.image)
                except docker.errors.ImageNotFound:
                    raise RuntimeError(
                        f"镜像 {self.image} 不存在且无法拉取。"
                        f"请先构建镜像: docker build -t {self.image} -f Dockerfile.sandbox ."
                    )
            
            # 创建并启动容器
            self.container = self.client.containers.run(**container_config)
            self._is_running = True
            
            return self.container.id
            
        except docker.errors.DockerException as e:
            raise RuntimeError(f"启动沙箱容器失败: {e}")
    
    async def execute(
        self,
        command: str,
        timeout: int = 30,
        working_dir: str = "/workspace"
    ) -> Tuple[int, str]:
        """
        在容器中执行命令
        
        Args:
            command: 要执行的命令
            timeout: 超时秒数
            working_dir: 命令执行的工作目录
        
        Returns:
            (退出码, 输出)
        """
        if not self._is_running or not self.container:
            raise RuntimeError("沙箱容器未启动，请先调用 start()")
        
        try:
            # 刷新容器状态
            self.container.reload()
            if self.container.status != "running":
                raise RuntimeError(f"容器状态异常: {self.container.status}")
            
            # 在容器中执行命令
            exec_result = self.container.exec_run(
                cmd=["bash", "-c", command],
                workdir=working_dir,
                user="sandbox",
                demux=True,  # 分离 stdout 和 stderr
                environment={"HOME": "/home/sandbox"},
            )
            
            # 处理输出
            exit_code = exec_result.exit_code
            stdout, stderr = exec_result.output
            
            output_parts = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                output_parts.append(stderr.decode("utf-8", errors="replace"))
            
            output = "\n".join(output_parts) if output_parts else ""
            
            return exit_code, output
            
        except docker.errors.APIError as e:
            raise RuntimeError(f"执行命令失败: {e}")
    
    async def execute_with_timeout(
        self,
        command: str,
        timeout: int = 30,
        working_dir: str = "/workspace"
    ) -> Tuple[int, str]:
        """
        在容器中执行命令（带超时保护）
        
        Args:
            command: 要执行的命令
            timeout: 超时秒数
            working_dir: 命令执行的工作目录
        
        Returns:
            (退出码, 输出)
        """
        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._execute_sync(command, working_dir)
                ),
                timeout=timeout
            )
            return result
        except asyncio.TimeoutError:
            return -1, f"命令执行超时（{timeout} 秒）"
    
    def _execute_sync(
        self,
        command: str,
        working_dir: str = "/workspace"
    ) -> Tuple[int, str]:
        """同步执行命令（供线程池调用）"""
        if not self._is_running or not self.container:
            raise RuntimeError("沙箱容器未启动")
        
        self.container.reload()
        if self.container.status != "running":
            raise RuntimeError(f"容器状态异常: {self.container.status}")
        
        exec_result = self.container.exec_run(
            cmd=["bash", "-c", command],
            workdir=working_dir,
            user="sandbox",
            demux=True,
            environment={"HOME": "/home/sandbox"},
        )
        
        exit_code = exec_result.exit_code
        stdout, stderr = exec_result.output
        
        output_parts = []
        if stdout:
            output_parts.append(stdout.decode("utf-8", errors="replace"))
        if stderr:
            output_parts.append(stderr.decode("utf-8", errors="replace"))
        
        return exit_code, "\n".join(output_parts) if output_parts else ""
    
    async def copy_to(self, local_path: str, container_path: str = "/workspace") -> bool:
        """
        复制文件到容器
        
        Args:
            local_path: 本地文件路径
            container_path: 容器内目标路径
        
        Returns:
            是否成功
        """
        if not self._is_running or not self.container:
            raise RuntimeError("沙箱容器未启动")
        
        local_path = Path(local_path).resolve()
        if not local_path.exists():
            raise FileNotFoundError(f"本地文件不存在: {local_path}")
        
        try:
            # 创建 tar 归档
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                tar.add(str(local_path), arcname=local_path.name)
            tar_stream.seek(0)
            
            # 复制到容器
            self.container.put_archive(container_path, tar_stream)
            return True
            
        except docker.errors.APIError as e:
            raise RuntimeError(f"复制文件到容器失败: {e}")
    
    async def copy_from(self, container_path: str, local_path: str) -> bool:
        """
        从容器复制文件
        
        Args:
            container_path: 容器内文件路径
            local_path: 本地目标路径
        
        Returns:
            是否成功
        """
        if not self._is_running or not self.container:
            raise RuntimeError("沙箱容器未启动")
        
        local_path = Path(local_path).resolve()
        
        try:
            # 从容器获取文件
            bits, stat = self.container.get_archive(container_path)
            
            # 解压 tar 归档
            tar_stream = io.BytesIO()
            for chunk in bits:
                tar_stream.write(chunk)
            tar_stream.seek(0)
            
            with tarfile.open(fileobj=tar_stream, mode='r') as tar:
                # 确保目标目录存在
                local_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 提取文件
                for member in tar.getmembers():
                    if member.isfile():
                        # 直接提取到目标路径
                        f = tar.extractfile(member)
                        if f:
                            with open(local_path, 'wb') as out:
                                out.write(f.read())
                            return True
            
            return False
            
        except docker.errors.NotFound:
            raise FileNotFoundError(f"容器内文件不存在: {container_path}")
        except docker.errors.APIError as e:
            raise RuntimeError(f"从容器复制文件失败: {e}")
    
    async def stop(self):
        """停止并删除容器"""
        if self.container:
            try:
                self.container.reload()
                if self.container.status == "running":
                    self.container.stop(timeout=5)
                self.container.remove(force=True)
            except docker.errors.NotFound:
                pass  # 容器已经不存在
            except docker.errors.APIError:
                pass  # 忽略其他错误
            finally:
                self.container = None
                self._is_running = False
    
    def is_running(self) -> bool:
        """检查容器是否在运行"""
        if not self._is_running or not self.container:
            return False
        try:
            self.container.reload()
            return self.container.status == "running"
        except docker.errors.NotFound:
            self._is_running = False
            self.container = None
            return False
    
    def __del__(self):
        """析构时尝试清理容器"""
        if self.container:
            try:
                self.container.remove(force=True)
            except:
                pass


# =====================
# 全局沙箱实例（用于热容器复用）
# =====================
_sandbox_instance: Optional[DockerSandbox] = None


def _get_sandbox_config() -> dict:
    """从 config.yaml 读取沙箱配置"""
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent.parent / "config.yaml"
    default_config = {
        "enabled": False,
        "image": "personalassistant-sandbox:latest",
        "memory_limit": "512m",
        "cpu_limit": 1.0,
        "network": "none",
        "workspace_mount": "./data/workspace"
    }
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            sandbox_config = config.get("sandbox", {})
            return {**default_config, **sandbox_config}
    except Exception:
        return default_config


def get_sandbox() -> DockerSandbox:
    """获取全局沙箱实例（单例模式）"""
    global _sandbox_instance
    
    if _sandbox_instance is None:
        config = _get_sandbox_config()
        _sandbox_instance = DockerSandbox(
            image=config.get("image", "personalassistant-sandbox:latest"),
            memory_limit=config.get("memory_limit", "512m"),
            cpu_limit=config.get("cpu_limit", 1.0),
            network_mode=config.get("network", "none"),
            workspace_mount=config.get("workspace_mount")
        )
    
    return _sandbox_instance


def is_sandbox_enabled() -> bool:
    """检查沙箱是否启用"""
    config = _get_sandbox_config()
    return config.get("enabled", False)



# 工具注册已全部移至 tools/shell.py
# 本模块仅提供 DockerSandbox 类和辅助函数：get_sandbox(), is_sandbox_enabled(), _get_sandbox_config()
