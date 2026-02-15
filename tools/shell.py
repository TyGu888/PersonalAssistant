from tools.registry import registry
import subprocess
import shlex
import os
import asyncio
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

# 延迟导入沙箱模块（避免循环导入）
_sandbox_module = None

def _get_sandbox_module():
    """延迟加载沙箱模块"""
    global _sandbox_module
    if _sandbox_module is None:
        from tools import sandbox
        _sandbox_module = sandbox
    return _sandbox_module


# 工作区目录（相对于项目根目录）
WORKSPACE_DIR = "./data/workspace"

# Shell 会话相关常量
DEFAULT_SESSION_TIMEOUT_MINUTES = 30
MAX_OUTPUT_LENGTH = 4000
COMMAND_MARKER_PREFIX = "___CMD_DONE___"
COMMAND_TIMEOUT_SECONDS = 60

# 危险命令黑名单（部分匹配）
DANGEROUS_COMMANDS = [
    'rm -rf /',
    'rm -rf / ',
    'rm -rf /*',
    'mkfs',
    'dd if=',
    ':(){',
    'sudo rm',
    'sudo mkfs',
    'sudo dd',
    'format',
    'fdisk',
    'parted',
    'mkfs.ext',
    'mkfs.ntfs',
    'mkfs.vfat',
    'shutdown',
    'reboot',
    'halt',
    'poweroff',
    'init 0',
    'init 6',
]


def _get_workspace_path() -> Path:
    """获取工作区目录的绝对路径"""
    project_root = Path(__file__).parent.parent
    workspace_path = project_root / WORKSPACE_DIR.lstrip("./")
    return workspace_path.resolve()


def _get_default_working_dir() -> Path:
    """获取默认工作目录（项目根目录或工作区）"""
    project_root = Path(__file__).parent.parent
    workspace_path = _get_workspace_path()
    
    # 优先使用工作区，如果不存在则使用项目根目录
    if workspace_path.exists():
        return workspace_path
    return project_root


def _check_dangerous_command(command: str) -> tuple[bool, Optional[str]]:
    """
    检查命令是否包含危险操作
    
    返回: (is_dangerous, reason)
    """
    command_lower = command.lower().strip()
    
    # 检查黑名单
    for dangerous in DANGEROUS_COMMANDS:
        if dangerous.lower() in command_lower:
            return True, f"检测到危险命令模式: {dangerous}"
    
    # 检查是否尝试删除根目录
    if 'rm' in command_lower:
        # 检查是否包含 / 或 /* 等危险路径
        parts = shlex.split(command)
        for part in parts:
            if part in ['/', '/*', '/.', '/..']:
                return True, "禁止删除根目录或系统关键路径"
    
    # 检查是否尝试使用 sudo（除非明确允许）
    if command_lower.startswith('sudo '):
        return True, "禁止使用 sudo 执行命令"
    
    return False, None


async def _run_command_in_sandbox(
    command: str,
    working_dir: str = None,
    timeout: int = 30,
    sandbox_module = None
) -> str:
    """
    在 Docker 沙箱中执行命令
    
    Args:
        command: 要执行的命令
        working_dir: 工作目录（在沙箱中默认为 /workspace）
        timeout: 超时秒数
        sandbox_module: 沙箱模块引用
    
    Returns:
        格式化的命令执行结果
    """
    try:
        sandbox = sandbox_module.get_sandbox()
        
        # 如果沙箱未运行，自动启动
        if not sandbox.is_running():
            await sandbox.start()
        
        # 确定工作目录（沙箱中的路径）
        container_working_dir = "/workspace"
        if working_dir:
            # 如果指定了工作目录，转换为沙箱内的相对路径
            # 这里简化处理，直接使用 /workspace
            container_working_dir = working_dir if working_dir.startswith("/") else f"/workspace/{working_dir}"
        
        # 在沙箱中执行命令
        exit_code, output = await sandbox.execute_with_timeout(
            command=command,
            timeout=timeout,
            working_dir=container_working_dir
        )
        
        # 截断过长输出
        MAX_OUTPUT_LENGTH = 2000
        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[:MAX_OUTPUT_LENGTH] + f"\n... (输出已截断，共 {len(output)} 字符)"
        
        # 格式化返回结果
        result_lines = [
            f"命令: {command}",
            f"工作目录: {container_working_dir} (沙箱)",
            f"执行环境: Docker 沙箱",
            f"退出码: {exit_code}",
        ]
        
        if output:
            result_lines.append("输出:")
            result_lines.append(output)
        else:
            result_lines.append("输出: (无)")
        
        return "\n".join(result_lines)
        
    except Exception as e:
        return f"沙箱执行错误: {e}\n命令: {command}"


@registry.register(
    name="run_command",
    description=(
        "Execute a shell command. "
        "When sandbox is enabled (check current config), commands run inside a Docker container by default "
        "(isolated environment at /workspace, with the host's data/workspace mounted). "
        "Set use_sandbox=false to run on the host machine instead (required for host-only tools like screencapture). "
        "Set use_sandbox=true to force sandbox even if disabled by default. "
        "Screenshots should be saved to data/screenshots/ (e.g. screencapture -x data/screenshots/shot.png). "
        "The framework auto-detects image paths in output and shows them to the LLM. "
        "This tool does NOT preserve state between calls (cd, export, etc. are lost). "
        "For stateful shell sessions, use the shell_session tool."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "working_dir": {"type": "string", "description": "Working directory (default: data/workspace on host, /workspace in sandbox)"},
            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
            "use_sandbox": {"type": "boolean", "description": "true=force sandbox, false=force host, omit=use config default (currently sandbox is enabled by config)"}
        },
        "required": ["command"]
    }
)
async def run_command(command: str, working_dir: str = None, timeout: int = 30, use_sandbox: bool = None, context=None) -> str:
    """
    执行 shell 命令（带安全限制）
    
    安全限制:
    1. 命令黑名单检查
    2. 超时限制（默认 30 秒）
    3. 输出截断（最多 2000 字符）
    4. 工作目录限制
    5. 可选的 Docker 沙箱隔离
    
    返回格式:
    命令: {command}
    退出码: {exit_code}
    输出:
    {stdout/stderr}
    """
    try:
        # 0. 判断是否使用沙箱
        sandbox_module = _get_sandbox_module()
        should_use_sandbox = use_sandbox
        
        # 如果未明确指定，根据配置决定
        if should_use_sandbox is None:
            should_use_sandbox = sandbox_module.is_sandbox_enabled()
        
        # 如果使用沙箱，委托给沙箱执行
        if should_use_sandbox:
            return await _run_command_in_sandbox(
                command=command,
                working_dir=working_dir,
                timeout=timeout,
                sandbox_module=sandbox_module
            )
        
        # 1. 检查危险命令（仅在宿主机执行时检查）
        is_dangerous, reason = _check_dangerous_command(command)
        if is_dangerous:
            return f"错误: {reason}\n命令: {command}"
        
        # 2. 确定工作目录
        if working_dir:
            # 验证工作目录路径
            try:
                work_path = Path(working_dir).resolve()
                if not work_path.exists() or not work_path.is_dir():
                    return f"错误: 工作目录不存在或不是目录: {working_dir}\n命令: {command}"
            except Exception as e:
                return f"错误: 无效的工作目录路径: {working_dir}\n命令: {command}"
        else:
            work_path = _get_default_working_dir()
        
        # 3. 执行命令
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(work_path),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='replace'
            )
            
            exit_code = result.returncode
            
            # 4. 处理输出（截断到 2000 字符）
            output = result.stdout if result.stdout else ""
            error_output = result.stderr if result.stderr else ""
            
            combined_output = ""
            if output:
                combined_output += output
            if error_output:
                if combined_output:
                    combined_output += "\n"
                combined_output += error_output
            
            MAX_OUTPUT_LENGTH = 2000
            if len(combined_output) > MAX_OUTPUT_LENGTH:
                truncated = combined_output[:MAX_OUTPUT_LENGTH]
                combined_output = truncated + f"\n... (输出已截断，共 {len(combined_output)} 字符)"
            
            # 5. 格式化返回结果
            result_lines = [
                f"命令: {command}",
                f"工作目录: {work_path}",
                f"退出码: {exit_code}",
            ]
            
            if combined_output:
                result_lines.append("输出:")
                result_lines.append(combined_output)
            else:
                result_lines.append("输出: (无)")
            
            return "\n".join(result_lines)
            
        except subprocess.TimeoutExpired:
            return f"错误: 命令执行超时（{timeout} 秒）\n命令: {command}"
        
        except FileNotFoundError:
            return f"错误: 命令不存在或无法找到可执行文件\n命令: {command}"
        
        except subprocess.SubprocessError as e:
            return f"错误: 执行命令时出错 - {str(e)}\n命令: {command}"
        
    except Exception as e:
        return f"错误: 未知错误 - {str(e)}\n命令: {command}"


# ============================================================
# 持久化 Shell 会话实现
# ============================================================

class ShellSession:
    """
    持久化 Shell 会话
    
    维护一个持久的 shell 进程，保持工作目录和环境变量状态
    """
    
    def __init__(self, session_id: str, timeout_minutes: int = DEFAULT_SESSION_TIMEOUT_MINUTES):
        self.session_id = session_id
        self.process: Optional[asyncio.subprocess.Process] = None
        self.created_at = datetime.now()
        self.last_used = datetime.now()
        self.timeout = timedelta(minutes=timeout_minutes)
        self._lock = asyncio.Lock()
        self._current_dir = str(_get_default_working_dir())
        self._is_running = False
    
    async def start(self) -> bool:
        """
        启动 shell 进程
        
        返回: 是否启动成功
        """
        try:
            # 使用 bash，设置无限制的历史和提示符
            self.process = await asyncio.create_subprocess_exec(
                'bash', '--norc', '--noprofile',
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._current_dir,
                env={
                    **os.environ,
                    'PS1': '',  # 禁用提示符
                    'PS2': '',
                    'TERM': 'dumb',
                }
            )
            self._is_running = True
            return True
        except Exception as e:
            self._is_running = False
            return False
    
    async def execute(self, command: str, timeout: int = COMMAND_TIMEOUT_SECONDS) -> tuple[bool, str]:
        """
        在会话中执行命令
        
        参数:
        - command: 要执行的命令
        - timeout: 超时秒数
        
        返回: (success, output)
        """
        if not self._is_running or self.process is None:
            return False, "会话未运行"
        
        if self.process.stdin is None or self.process.stdout is None:
            return False, "会话 I/O 不可用"
        
        async with self._lock:
            self.last_used = datetime.now()
            
            # 生成唯一的命令结束标记
            marker = f"{COMMAND_MARKER_PREFIX}{uuid.uuid4().hex[:8]}"
            
            # 构建带标记的命令
            # 使用 echo 输出标记和退出码
            full_command = f"{command}; __exit_code__=$?; echo ''; echo '{marker}'$__exit_code__\n"
            
            try:
                # 发送命令
                self.process.stdin.write(full_command.encode('utf-8'))
                await self.process.stdin.drain()
                
                # 读取输出直到看到标记
                output_lines = []
                exit_code = 0
                
                async def read_until_marker():
                    nonlocal exit_code
                    while True:
                        line = await self.process.stdout.readline()
                        if not line:
                            # 进程已结束
                            self._is_running = False
                            break
                        
                        line_str = line.decode('utf-8', errors='replace').rstrip('\n\r')
                        
                        # 检查是否是结束标记
                        if line_str.startswith(marker):
                            # 提取退出码
                            try:
                                exit_code = int(line_str[len(marker):])
                            except ValueError:
                                exit_code = 0
                            break
                        
                        output_lines.append(line_str)
                
                try:
                    await asyncio.wait_for(read_until_marker(), timeout=timeout)
                except asyncio.TimeoutError:
                    return False, f"命令执行超时（{timeout} 秒）"
                
                # 合并输出
                output = '\n'.join(output_lines)
                
                # 截断输出
                if len(output) > MAX_OUTPUT_LENGTH:
                    output = output[:MAX_OUTPUT_LENGTH] + f"\n... (输出已截断，共 {len(output)} 字符)"
                
                # 更新当前目录（通过 pwd 命令）
                await self._update_current_dir()
                
                # 格式化结果
                result = f"退出码: {exit_code}\n"
                if output:
                    result += f"输出:\n{output}"
                else:
                    result += "输出: (无)"
                
                return True, result
                
            except Exception as e:
                return False, f"执行错误: {str(e)}"
    
    async def _update_current_dir(self):
        """更新记录的当前目录"""
        if not self._is_running or self.process is None:
            return
        
        if self.process.stdin is None or self.process.stdout is None:
            return
        
        try:
            marker = f"{COMMAND_MARKER_PREFIX}pwd{uuid.uuid4().hex[:8]}"
            pwd_command = f"pwd; echo '{marker}'\n"
            
            self.process.stdin.write(pwd_command.encode('utf-8'))
            await self.process.stdin.drain()
            
            async def read_pwd():
                while True:
                    line = await self.process.stdout.readline()
                    if not line:
                        break
                    
                    line_str = line.decode('utf-8', errors='replace').rstrip('\n\r')
                    
                    if line_str.startswith(marker):
                        break
                    
                    if line_str and not line_str.startswith(COMMAND_MARKER_PREFIX):
                        self._current_dir = line_str
            
            await asyncio.wait_for(read_pwd(), timeout=5)
        except:
            pass  # 静默处理错误
    
    async def stop(self):
        """停止会话"""
        self._is_running = False
        if self.process is not None:
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self.process.kill()
            except:
                pass
            self.process = None
    
    def is_expired(self) -> bool:
        """检查会话是否已超时"""
        return datetime.now() - self.last_used > self.timeout
    
    def is_running(self) -> bool:
        """检查会话是否在运行"""
        return self._is_running and self.process is not None
    
    def get_info(self) -> dict:
        """获取会话信息"""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat(),
            "current_dir": self._current_dir,
            "is_running": self.is_running(),
            "timeout_minutes": int(self.timeout.total_seconds() / 60),
            "idle_seconds": int((datetime.now() - self.last_used).total_seconds()),
        }


class ShellSessionManager:
    """
    Shell 会话管理器
    
    管理所有 Shell 会话，支持创建、查询、清理等操作
    """
    
    def __init__(self):
        self._sessions: dict[str, ShellSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
    
    async def create_session(self, timeout_minutes: int = DEFAULT_SESSION_TIMEOUT_MINUTES) -> tuple[bool, str, str]:
        """
        创建新会话
        
        返回: (success, session_id, message)
        """
        async with self._lock:
            session_id = f"shell_{uuid.uuid4().hex[:8]}"
            session = ShellSession(session_id, timeout_minutes)
            
            if await session.start():
                self._sessions[session_id] = session
                
                # 启动清理任务（如果还没启动）
                self._ensure_cleanup_task()
                
                return True, session_id, f"会话已创建，ID: {session_id}"
            else:
                return False, "", "无法启动 shell 进程"
    
    async def execute(self, session_id: str, command: str, timeout: int = COMMAND_TIMEOUT_SECONDS) -> tuple[bool, str]:
        """
        在指定会话中执行命令
        
        返回: (success, output)
        """
        session = self._sessions.get(session_id)
        if session is None:
            return False, f"会话不存在: {session_id}"
        
        if not session.is_running():
            # 尝试重新启动
            if not await session.start():
                return False, "会话已停止且无法重新启动"
        
        if session.is_expired():
            await self.stop_session(session_id)
            return False, "会话已超时并被关闭"
        
        return await session.execute(command, timeout)
    
    async def stop_session(self, session_id: str) -> tuple[bool, str]:
        """
        停止并删除会话
        
        返回: (success, message)
        """
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return False, f"会话不存在: {session_id}"
            
            await session.stop()
            return True, f"会话已关闭: {session_id}"
    
    def list_sessions(self) -> list[dict]:
        """列出所有活跃会话"""
        return [session.get_info() for session in self._sessions.values()]
    
    async def cleanup_expired(self):
        """清理过期会话"""
        async with self._lock:
            expired_ids = [
                sid for sid, session in self._sessions.items()
                if session.is_expired() or not session.is_running()
            ]
            
            for sid in expired_ids:
                session = self._sessions.pop(sid, None)
                if session:
                    await session.stop()
    
    def _ensure_cleanup_task(self):
        """确保清理任务正在运行"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def _cleanup_loop(self):
        """定期清理过期会话"""
        while True:
            await asyncio.sleep(60)  # 每分钟检查一次
            await self.cleanup_expired()
            
            # 如果没有会话了，退出循环
            if not self._sessions:
                break
    
    async def shutdown(self):
        """关闭所有会话"""
        async with self._lock:
            for session in self._sessions.values():
                await session.stop()
            self._sessions.clear()
            
            if self._cleanup_task and not self._cleanup_task.done():
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except asyncio.CancelledError:
                    pass


# 全局会话管理器
_session_manager = ShellSessionManager()


# ============================================================
# Shell 会话 Tool (merged: start/exec/stop/list)
# ============================================================

@registry.register(
    name="shell_session",
    description=(
        "Manage persistent shell sessions that preserve working directory and environment variables across commands. "
        "Actions: start (create new session), exec (run command in session), stop (close session), list (show active sessions)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "exec", "stop", "list"],
                "description": "Action to perform"
            },
            "session_id": {"type": "string", "description": "Session ID (required for exec/stop)"},
            "command": {"type": "string", "description": "Command to execute (required for exec)"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (for start: session timeout in minutes, default 30; for exec: command timeout in seconds, default 60)"}
        },
        "required": ["action"]
    }
)
async def shell_session(action: str, session_id: str = None, command: str = None, timeout: int = None, context=None) -> str:
    """Manage persistent shell sessions."""

    if action == "start":
        timeout_minutes = timeout if timeout is not None else 30
        success, sid, message = await _session_manager.create_session(timeout_minutes)
        if success:
            return f"✓ {message}\n提示: 使用 shell_session(action='exec', session_id='{sid}', command='...') 在此会话中执行命令"
        else:
            return f"✗ 创建会话失败: {message}"

    elif action == "exec":
        if not session_id:
            return "错误: exec 操作需要 session_id"
        if not command:
            return "错误: exec 操作需要 command"
        # 安全检查
        is_dangerous, reason = _check_dangerous_command(command)
        if is_dangerous:
            return f"✗ 错误: {reason}\n命令: {command}"
        exec_timeout = timeout if timeout is not None else 60
        success, output = await _session_manager.execute(session_id, command, exec_timeout)
        if success:
            sessions = _session_manager.list_sessions()
            current_session = next((s for s in sessions if s["session_id"] == session_id), None)
            current_dir = current_session["current_dir"] if current_session else "未知"
            return f"命令: {command}\n工作目录: {current_dir}\n{output}"
        else:
            return f"✗ 执行失败: {output}\n命令: {command}"

    elif action == "stop":
        if not session_id:
            return "错误: stop 操作需要 session_id"
        success, message = await _session_manager.stop_session(session_id)
        if success:
            return f"✓ {message}"
        else:
            return f"✗ {message}"

    elif action == "list":
        sessions = _session_manager.list_sessions()
        if not sessions:
            return "当前没有活跃的 Shell 会话"
        lines = [f"活跃会话数: {len(sessions)}", ""]
        for session in sessions:
            status = "运行中" if session["is_running"] else "已停止"
            lines.append(f"会话 ID: {session['session_id']}")
            lines.append(f"  状态: {status}")
            lines.append(f"  工作目录: {session['current_dir']}")
            lines.append(f"  创建时间: {session['created_at']}")
            lines.append(f"  最后使用: {session['last_used']}")
            lines.append(f"  空闲时间: {session['idle_seconds']} 秒")
            lines.append(f"  超时设置: {session['timeout_minutes']} 分钟")
            lines.append("")
        return "\n".join(lines)

    else:
        return f"错误: 未知 action '{action}'。可用: start, exec, stop, list"


# =====================
# Docker 沙箱管理 Tool (merged: status/stop/copy_to/copy_from)
# sandbox_exec 和 sandbox_start 已移除（run_command 自动路由到沙箱）
# =====================


@registry.register(
    name="sandbox",
    description=(
        "Manage the Docker sandbox container. "
        "Actions: status (view sandbox state), stop (stop container), copy_to (copy file into sandbox), copy_from (copy file out of sandbox)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "stop", "copy_to", "copy_from"],
                "description": "Action to perform"
            },
            "local_path": {"type": "string", "description": "Local file path (for copy_to/copy_from)"},
            "container_path": {"type": "string", "description": "Container file path (for copy_to: default /workspace, for copy_from: required)"}
        },
        "required": ["action"]
    }
)
async def sandbox(action: str, local_path: str = None, container_path: str = None, context=None) -> str:
    """Manage the Docker sandbox container."""

    if action == "status":
        try:
            sandbox_module = _get_sandbox_module()
            config = sandbox_module._get_sandbox_config()
            sb = sandbox_module.get_sandbox()
            is_running = sb.is_running()

            status_lines = [
                "=== Docker 沙箱状态 ===",
                f"配置启用: {'是' if config.get('enabled') else '否'}",
                f"容器运行: {'是' if is_running else '否'}",
                f"镜像: {config.get('image')}",
                f"内存限制: {config.get('memory_limit')}",
                f"CPU 限制: {config.get('cpu_limit')}",
                f"网络模式: {config.get('network')}",
            ]
            if is_running and sb.container:
                status_lines.append(f"容器 ID: {sb.container.id[:12]}")
            return "\n".join(status_lines)
        except Exception as e:
            return f"获取状态失败: {e}"

    elif action == "stop":
        sandbox_module = _get_sandbox_module()
        try:
            sb = sandbox_module.get_sandbox()
            if not sb.is_running():
                return "沙箱容器未运行"
            await sb.stop()
            sandbox_module._sandbox_instance = None
            return "沙箱容器已停止并清理"
        except Exception as e:
            return f"停止沙箱失败: {e}"

    elif action == "copy_to":
        if not local_path:
            return "错误: copy_to 操作需要 local_path"
        cp = container_path or "/workspace"
        try:
            sandbox_module = _get_sandbox_module()
            sb = sandbox_module.get_sandbox()
            if not sb.is_running():
                return "沙箱容器未运行，请先执行一条命令以自动启动"
            await sb.copy_to(local_path, cp)
            return f"已复制文件到沙箱\n本地: {local_path}\n容器: {cp}"
        except FileNotFoundError as e:
            return f"文件不存在: {e}"
        except Exception as e:
            return f"复制文件失败: {e}"

    elif action == "copy_from":
        if not container_path:
            return "错误: copy_from 操作需要 container_path"
        if not local_path:
            return "错误: copy_from 操作需要 local_path"
        try:
            sandbox_module = _get_sandbox_module()
            sb = sandbox_module.get_sandbox()
            if not sb.is_running():
                return "沙箱容器未运行，请先执行一条命令以自动启动"
            await sb.copy_from(container_path, local_path)
            return f"已从沙箱复制文件\n容器: {container_path}\n本地: {local_path}"
        except FileNotFoundError as e:
            return f"文件不存在: {e}"
        except Exception as e:
            return f"复制文件失败: {e}"

    else:
        return f"错误: 未知 action '{action}'。可用: status, stop, copy_to, copy_from"
