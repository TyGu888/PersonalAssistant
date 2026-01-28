from tools.registry import registry
import subprocess
import shlex
import os
from pathlib import Path
from typing import Optional


# 工作区目录（相对于项目根目录）
WORKSPACE_DIR = "./data/workspace"

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


@registry.register(
    name="run_command",
    description="执行 shell 命令（有安全限制）",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "working_dir": {"type": "string", "description": "工作目录（可选，默认为项目根目录或工作区）"},
            "timeout": {"type": "integer", "description": "超时秒数", "default": 30}
        },
        "required": ["command"]
    }
)
async def run_command(command: str, working_dir: str = None, timeout: int = 30, context=None) -> str:
    """
    执行 shell 命令（带安全限制）
    
    安全限制:
    1. 命令黑名单检查
    2. 超时限制（默认 30 秒）
    3. 输出截断（最多 2000 字符）
    4. 工作目录限制
    
    返回格式:
    命令: {command}
    退出码: {exit_code}
    输出:
    {stdout/stderr}
    """
    try:
        # 1. 检查危险命令
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
            # 使用 subprocess.run 执行命令
            # shell=True 允许使用 shell 特性，但需要小心处理
            # 使用 shlex.split 来安全地分割命令
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(work_path),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='replace'  # 处理编码错误
            )
            
            exit_code = result.returncode
            
            # 4. 处理输出（截断到 2000 字符）
            output = result.stdout if result.stdout else ""
            error_output = result.stderr if result.stderr else ""
            
            # 合并 stdout 和 stderr
            combined_output = ""
            if output:
                combined_output += output
            if error_output:
                if combined_output:
                    combined_output += "\n"
                combined_output += error_output
            
            # 截断输出
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
