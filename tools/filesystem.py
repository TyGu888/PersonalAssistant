from tools.registry import registry
import os
from pathlib import Path
from typing import Optional


# 工作区目录（相对于项目根目录）
WORKSPACE_DIR = "./data/workspace"


def _get_workspace_path() -> Path:
    """获取工作区目录的绝对路径"""
    # 获取项目根目录（tools 目录的父目录）
    project_root = Path(__file__).parent.parent
    workspace_path = project_root / WORKSPACE_DIR.lstrip("./")
    return workspace_path.resolve()


def _ensure_workspace():
    """确保工作区目录存在，不存在则创建"""
    workspace_path = _get_workspace_path()
    workspace_path.mkdir(parents=True, exist_ok=True)


def _safe_path(filename: str) -> Path:
    """
    验证并返回安全路径
    
    安全规则：
    1. 路径必须限制在工作区目录内
    2. 禁止使用 ../ 或绝对路径访问工作区外的文件
    3. 使用 os.path.realpath() 规范化路径并检查
    
    如果路径不安全，抛出 ValueError 异常
    """
    if not filename:
        raise ValueError("文件名不能为空")
    
    # 规范化输入路径（移除多余的斜杠等）
    filename = filename.strip().lstrip("/")
    
    # 如果包含 .. 或绝对路径，直接拒绝
    if ".." in filename or os.path.isabs(filename):
        raise ValueError(f"禁止使用相对路径 '..' 或绝对路径访问工作区外的文件: {filename}")
    
    # 获取工作区绝对路径
    workspace_path = _get_workspace_path()
    
    # 构建完整路径
    full_path = (workspace_path / filename).resolve()
    
    # 使用 realpath 规范化路径，防止符号链接逃逸
    real_path = Path(os.path.realpath(str(full_path)))
    real_workspace = Path(os.path.realpath(str(workspace_path)))
    
    # 检查路径是否在工作区内
    try:
        # 使用 relative_to 检查路径是否在工作区内
        real_path.relative_to(real_workspace)
    except ValueError:
        # relative_to 失败说明路径不在工作区内
        raise ValueError(f"路径超出工作区范围: {filename}")
    
    return real_path


@registry.register(
    name="create_file",
    description="在工作区创建或覆盖文件",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "文件名（相对于工作区）"},
            "content": {"type": "string", "description": "文件内容"}
        },
        "required": ["filename", "content"]
    }
)
async def create_file(filename: str, content: str, context=None) -> str:
    """
    创建或覆盖文件
    
    流程:
    1. 确保工作区目录存在
    2. 验证路径安全性
    3. 创建父目录（如果不存在）
    4. 写入文件内容
    
    返回: "文件已创建: {filename}" 或错误信息
    """
    try:
        _ensure_workspace()
        safe_path = _safe_path(filename)
        
        # 创建父目录（如果不存在）
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 写入文件
        safe_path.write_text(content, encoding='utf-8')
        
        return f"文件已创建: {filename}"
    except ValueError as e:
        return f"错误: {str(e)}"
    except Exception as e:
        return f"错误: 创建文件失败 - {str(e)}"


@registry.register(
    name="read_file",
    description="读取工作区文件内容",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "文件名（相对于工作区）"}
        },
        "required": ["filename"]
    }
)
async def read_file(filename: str, context=None) -> str:
    """
    读取文件内容
    
    流程:
    1. 验证路径安全性
    2. 检查文件是否存在
    3. 读取并返回文件内容
    
    返回: 文件内容或错误信息
    """
    try:
        safe_path = _safe_path(filename)
        
        if not safe_path.exists():
            return f"错误: 文件不存在: {filename}"
        
        if not safe_path.is_file():
            return f"错误: 路径不是文件: {filename}"
        
        content = safe_path.read_text(encoding='utf-8')
        return content
    except ValueError as e:
        return f"错误: {str(e)}"
    except Exception as e:
        return f"错误: 读取文件失败 - {str(e)}"


@registry.register(
    name="list_files",
    description="列出工作区目录下的文件和子目录",
    parameters={
        "type": "object",
        "properties": {
            "directory": {
                "type": "string",
                "description": "目录路径（相对于工作区，默认为空表示根目录）",
                "default": ""
            }
        },
        "required": []
    }
)
async def list_files(directory: str = "", context=None) -> str:
    """
    列出目录下的文件和子目录
    
    流程:
    1. 验证路径安全性
    2. 检查路径是否存在且为目录
    3. 列出目录内容
    
    返回: 文件列表（每行一个）或错误信息
    """
    try:
        if not directory:
            # 空字符串表示工作区根目录
            safe_path = _get_workspace_path()
        else:
            safe_path = _safe_path(directory)
        
        _ensure_workspace()
        
        if not safe_path.exists():
            return f"错误: 目录不存在: {directory if directory else '工作区根目录'}"
        
        if not safe_path.is_dir():
            return f"错误: 路径不是目录: {directory}"
        
        # 列出目录内容
        items = []
        for item in sorted(safe_path.iterdir()):
            if item.is_dir():
                items.append(f"[目录] {item.name}/")
            else:
                size = item.stat().st_size
                items.append(f"[文件] {item.name} ({size} 字节)")
        
        if not items:
            return f"目录为空: {directory if directory else '工作区根目录'}"
        
        header = f"目录内容 ({directory if directory else '工作区根目录'}):"
        return header + "\n" + "\n".join(items)
    except ValueError as e:
        return f"错误: {str(e)}"
    except Exception as e:
        return f"错误: 列出文件失败 - {str(e)}"


@registry.register(
    name="append_file",
    description="追加内容到工作区文件",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "文件名（相对于工作区）"},
            "content": {"type": "string", "description": "要追加的内容"}
        },
        "required": ["filename", "content"]
    }
)
async def append_file(filename: str, content: str, context=None) -> str:
    """
    追加内容到文件
    
    流程:
    1. 确保工作区目录存在
    2. 验证路径安全性
    3. 创建父目录（如果不存在）
    4. 追加内容到文件（文件不存在则创建）
    
    返回: "内容已追加到文件: {filename}" 或错误信息
    """
    try:
        _ensure_workspace()
        safe_path = _safe_path(filename)
        
        # 创建父目录（如果不存在）
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 追加内容（文件不存在则创建）
        with safe_path.open('a', encoding='utf-8') as f:
            f.write(content)
        
        return f"内容已追加到文件: {filename}"
    except ValueError as e:
        return f"错误: {str(e)}"
    except Exception as e:
        return f"错误: 追加内容失败 - {str(e)}"


@registry.register(
    name="delete_file",
    description="删除工作区文件或空目录",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "文件名或目录名（相对于工作区）"}
        },
        "required": ["filename"]
    }
)
async def delete_file(filename: str, context=None) -> str:
    """
    删除文件或空目录
    
    流程:
    1. 验证路径安全性
    2. 检查路径是否存在
    3. 删除文件或空目录（非空目录不删除）
    
    返回: "已删除: {filename}" 或错误信息
    """
    try:
        safe_path = _safe_path(filename)
        
        if not safe_path.exists():
            return f"错误: 文件或目录不存在: {filename}"
        
        # 防止删除工作区根目录本身
        workspace_path = _get_workspace_path()
        if safe_path.resolve() == workspace_path.resolve():
            return f"错误: 不能删除工作区根目录"
        
        if safe_path.is_file():
            safe_path.unlink()
            return f"已删除文件: {filename}"
        elif safe_path.is_dir():
            # 检查目录是否为空
            if any(safe_path.iterdir()):
                return f"错误: 目录非空，无法删除: {filename}"
            safe_path.rmdir()
            return f"已删除空目录: {filename}"
        else:
            return f"错误: 未知的文件类型: {filename}"
    except ValueError as e:
        return f"错误: {str(e)}"
    except Exception as e:
        return f"错误: 删除失败 - {str(e)}"


@registry.register(
    name="send_file",
    description="将工作区的文件发送给用户（作为附件）",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "文件名（相对于工作区）"},
            "message": {"type": "string", "description": "附带的消息（可选）", "default": ""}
        },
        "required": ["filename"]
    }
)
async def send_file(filename: str, message: str = "", context=None) -> str:
    """
    发送文件给用户
    
    流程:
    1. 验证路径安全性
    2. 检查文件是否存在
    3. 将文件路径添加到响应的 attachments 中
    
    注意: 这个 tool 会在 context 中标记需要发送的文件，
    由 Channel 层实际发送附件。
    
    返回: "文件将发送给用户: {filename}" 或错误信息
    """
    try:
        safe_path = _safe_path(filename)
        
        if not safe_path.exists():
            return f"错误: 文件不存在: {filename}"
        
        if not safe_path.is_file():
            return f"错误: 路径不是文件: {filename}"
        
        # 将文件路径存入 context，供后续处理
        if context and "pending_attachments" in context:
            context["pending_attachments"].append(str(safe_path))
        elif context:
            context["pending_attachments"] = [str(safe_path)]
        
        return f"文件准备发送: {filename}"
    except ValueError as e:
        return f"错误: {str(e)}"
    except Exception as e:
        return f"错误: 发送文件失败 - {str(e)}"
