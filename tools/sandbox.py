"""
Docker 沙箱模块 - 提供容器隔离执行环境

功能：
- 创建隔离的 Docker 容器执行命令
- 支持热容器复用（不用每次都创建新容器）
- 资源限制（内存、CPU）
- 超时保护
- 文件复制（双向）
"""

import docker
import asyncio
import tarfile
import io
import os
from typing import Optional, Tuple
from pathlib import Path
from tools.registry import registry


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


# =====================
# 注册 Tool
# =====================

@registry.register(
    name="sandbox_start",
    description="启动 Docker 沙箱容器",
    parameters={
        "type": "object",
        "properties": {
            "workspace_dir": {
                "type": "string",
                "description": "可选的工作目录挂载路径"
            }
        },
        "required": []
    }
)
async def sandbox_start(workspace_dir: str = None, context=None) -> str:
    """启动沙箱容器"""
    try:
        sandbox = get_sandbox()
        container_id = await sandbox.start(workspace_dir)
        return f"沙箱容器已启动\n容器 ID: {container_id[:12]}\n镜像: {sandbox.image}"
    except Exception as e:
        return f"启动沙箱失败: {e}"


@registry.register(
    name="sandbox_exec",
    description="在 Docker 沙箱中执行命令",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令"
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数",
                "default": 30
            },
            "working_dir": {
                "type": "string",
                "description": "工作目录",
                "default": "/workspace"
            }
        },
        "required": ["command"]
    }
)
async def sandbox_exec(
    command: str,
    timeout: int = 30,
    working_dir: str = "/workspace",
    context=None
) -> str:
    """在沙箱中执行命令"""
    try:
        sandbox = get_sandbox()
        
        # 如果沙箱未运行，自动启动
        if not sandbox.is_running():
            await sandbox.start()
        
        exit_code, output = await sandbox.execute_with_timeout(
            command=command,
            timeout=timeout,
            working_dir=working_dir
        )
        
        # 截断过长输出
        MAX_OUTPUT_LENGTH = 2000
        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[:MAX_OUTPUT_LENGTH] + f"\n... (输出已截断，共 {len(output)} 字符)"
        
        result_lines = [
            f"命令: {command}",
            f"工作目录: {working_dir}",
            f"退出码: {exit_code}",
        ]
        
        if output:
            result_lines.append("输出:")
            result_lines.append(output)
        else:
            result_lines.append("输出: (无)")
        
        return "\n".join(result_lines)
        
    except Exception as e:
        return f"执行命令失败: {e}"


@registry.register(
    name="sandbox_stop",
    description="停止 Docker 沙箱容器",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    }
)
async def sandbox_stop(context=None) -> str:
    """停止沙箱容器"""
    global _sandbox_instance
    
    try:
        if _sandbox_instance is None or not _sandbox_instance.is_running():
            return "沙箱容器未运行"
        
        await _sandbox_instance.stop()
        _sandbox_instance = None
        return "沙箱容器已停止并清理"
        
    except Exception as e:
        return f"停止沙箱失败: {e}"


@registry.register(
    name="sandbox_copy_to",
    description="复制文件到 Docker 沙箱容器",
    parameters={
        "type": "object",
        "properties": {
            "local_path": {
                "type": "string",
                "description": "本地文件路径"
            },
            "container_path": {
                "type": "string",
                "description": "容器内目标目录",
                "default": "/workspace"
            }
        },
        "required": ["local_path"]
    }
)
async def sandbox_copy_to(
    local_path: str,
    container_path: str = "/workspace",
    context=None
) -> str:
    """复制文件到沙箱"""
    try:
        sandbox = get_sandbox()
        
        if not sandbox.is_running():
            return "沙箱容器未运行，请先启动"
        
        await sandbox.copy_to(local_path, container_path)
        return f"已复制文件到沙箱\n本地: {local_path}\n容器: {container_path}"
        
    except FileNotFoundError as e:
        return f"文件不存在: {e}"
    except Exception as e:
        return f"复制文件失败: {e}"


@registry.register(
    name="sandbox_copy_from",
    description="从 Docker 沙箱容器复制文件",
    parameters={
        "type": "object",
        "properties": {
            "container_path": {
                "type": "string",
                "description": "容器内文件路径"
            },
            "local_path": {
                "type": "string",
                "description": "本地目标路径"
            }
        },
        "required": ["container_path", "local_path"]
    }
)
async def sandbox_copy_from(
    container_path: str,
    local_path: str,
    context=None
) -> str:
    """从沙箱复制文件"""
    try:
        sandbox = get_sandbox()
        
        if not sandbox.is_running():
            return "沙箱容器未运行，请先启动"
        
        await sandbox.copy_from(container_path, local_path)
        return f"已从沙箱复制文件\n容器: {container_path}\n本地: {local_path}"
        
    except FileNotFoundError as e:
        return f"文件不存在: {e}"
    except Exception as e:
        return f"复制文件失败: {e}"


@registry.register(
    name="sandbox_status",
    description="查看 Docker 沙箱状态",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    }
)
async def sandbox_status(context=None) -> str:
    """查看沙箱状态"""
    try:
        config = _get_sandbox_config()
        sandbox = get_sandbox()
        
        is_running = sandbox.is_running()
        
        status_lines = [
            "=== Docker 沙箱状态 ===",
            f"配置启用: {'是' if config.get('enabled') else '否'}",
            f"容器运行: {'是' if is_running else '否'}",
            f"镜像: {config.get('image')}",
            f"内存限制: {config.get('memory_limit')}",
            f"CPU 限制: {config.get('cpu_limit')}",
            f"网络模式: {config.get('network')}",
        ]
        
        if is_running and sandbox.container:
            status_lines.append(f"容器 ID: {sandbox.container.id[:12]}")
        
        return "\n".join(status_lines)
        
    except Exception as e:
        return f"获取状态失败: {e}"
