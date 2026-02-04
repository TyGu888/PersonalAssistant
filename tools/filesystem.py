from tools.registry import registry
import os
import re
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


@registry.register(
    name="edit_file",
    description="精确编辑文件：查找并替换指定字符串",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "文件名（相对于工作区）"},
            "old_string": {"type": "string", "description": "要替换的原始字符串（必须唯一，除非 replace_all=True）"},
            "new_string": {"type": "string", "description": "替换后的字符串"},
            "replace_all": {"type": "boolean", "description": "是否替换所有匹配", "default": False}
        },
        "required": ["filename", "old_string", "new_string"]
    }
)
async def edit_file(filename: str, old_string: str, new_string: str, replace_all: bool = False, context=None) -> str:
    """
    精确编辑文件：查找并替换指定字符串
    
    流程:
    1. 验证路径安全性
    2. 读取文件内容
    3. 检查 old_string 出现次数
    4. 如果不唯一且 replace_all=False，返回错误
    5. 执行替换并写回文件
    
    返回: "文件已编辑: {filename}" 或错误信息
    """
    try:
        safe_path = _safe_path(filename)
        
        if not safe_path.exists():
            return f"错误: 文件不存在: {filename}"
        
        if not safe_path.is_file():
            return f"错误: 路径不是文件: {filename}"
        
        # 读取文件内容
        content = safe_path.read_text(encoding='utf-8')
        
        # 检查 old_string 出现次数
        count = content.count(old_string)
        
        if count == 0:
            return f"错误: 未找到要替换的字符串"
        
        if count > 1 and not replace_all:
            return f"错误: 找到 {count} 处匹配，请提供更精确的字符串或设置 replace_all=True"
        
        # 执行替换
        if replace_all:
            new_content = content.replace(old_string, new_string)
            replaced_count = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            replaced_count = 1
        
        # 写回文件
        safe_path.write_text(new_content, encoding='utf-8')
        
        return f"文件已编辑: {filename}（替换了 {replaced_count} 处）"
    except ValueError as e:
        return f"错误: {str(e)}"
    except Exception as e:
        return f"错误: 编辑文件失败 - {str(e)}"


@registry.register(
    name="find_files",
    description="使用 glob 模式查找文件",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob 模式，如 '*.py' 或 '**/test_*.py'"},
            "directory": {"type": "string", "description": "搜索目录（相对于工作区，默认为空表示工作区根目录）", "default": ""}
        },
        "required": ["pattern"]
    }
)
async def find_files(pattern: str, directory: str = "", context=None) -> str:
    """
    使用 glob 模式查找文件
    
    流程:
    1. 验证路径安全性
    2. 使用 pathlib.Path.glob() 查找匹配文件
    3. 返回匹配的文件列表（相对于工作区的路径）
    
    返回: 匹配的文件列表或错误信息
    """
    try:
        _ensure_workspace()
        workspace_path = _get_workspace_path()
        
        # 确定搜索目录
        if not directory:
            search_path = workspace_path
        else:
            search_path = _safe_path(directory)
            if not search_path.is_dir():
                return f"错误: 目录不存在: {directory}"
        
        # 使用 glob 查找文件
        matches = []
        for match in search_path.glob(pattern):
            # 只返回文件，不返回目录
            if match.is_file():
                # 返回相对于工作区的路径
                rel_path = match.relative_to(workspace_path)
                matches.append(str(rel_path))
        
        if not matches:
            return f"未找到匹配 '{pattern}' 的文件"
        
        # 排序结果
        matches.sort()
        
        header = f"找到 {len(matches)} 个匹配文件:"
        return header + "\n" + "\n".join(matches)
    except ValueError as e:
        return f"错误: {str(e)}"
    except Exception as e:
        return f"错误: 查找文件失败 - {str(e)}"


@registry.register(
    name="grep_files",
    description="搜索文件内容",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "搜索模式（支持正则表达式）"},
            "directory": {"type": "string", "description": "搜索目录（相对于工作区）", "default": ""},
            "glob": {"type": "string", "description": "文件过滤模式，如 '*.py'", "default": "*"},
            "context_lines": {"type": "integer", "description": "显示匹配行前后的行数", "default": 2},
            "max_results": {"type": "integer", "description": "最大返回结果数", "default": 50}
        },
        "required": ["pattern"]
    }
)
async def grep_files(
    pattern: str,
    directory: str = "",
    glob: str = "*",
    context_lines: int = 2,
    max_results: int = 50,
    context=None
) -> str:
    """
    搜索文件内容
    
    流程:
    1. 遍历匹配 glob 的文件
    2. 使用 re.search() 搜索每行
    3. 返回匹配行 + 上下文
    4. 格式化输出（文件名:行号:内容）
    
    返回: 搜索结果或错误信息
    """
    try:
        _ensure_workspace()
        workspace_path = _get_workspace_path()
        
        # 确定搜索目录
        if not directory:
            search_path = workspace_path
        else:
            search_path = _safe_path(directory)
            if not search_path.is_dir():
                return f"错误: 目录不存在: {directory}"
        
        # 编译正则表达式
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"错误: 无效的正则表达式 - {str(e)}"
        
        results = []
        total_matches = 0
        
        # 使用递归 glob 模式
        glob_pattern = f"**/{glob}" if not glob.startswith("**/") else glob
        
        for file_path in search_path.glob(glob_pattern):
            if not file_path.is_file():
                continue
            
            # 尝试读取文件（跳过二进制文件）
            try:
                lines = file_path.read_text(encoding='utf-8').splitlines()
            except (UnicodeDecodeError, PermissionError):
                # 跳过无法读取的文件
                continue
            
            # 搜索匹配行
            file_matches = []
            for line_num, line in enumerate(lines, 1):
                if regex.search(line):
                    file_matches.append((line_num, line))
            
            if not file_matches:
                continue
            
            # 获取相对路径
            rel_path = file_path.relative_to(workspace_path)
            
            # 处理每个匹配，添加上下文
            for match_line_num, match_line in file_matches:
                if total_matches >= max_results:
                    break
                
                # 计算上下文范围
                start_line = max(1, match_line_num - context_lines)
                end_line = min(len(lines), match_line_num + context_lines)
                
                # 构建结果块
                result_block = [f"--- {rel_path} ---"]
                for ln in range(start_line, end_line + 1):
                    prefix = ">" if ln == match_line_num else " "
                    result_block.append(f"{prefix} {ln}: {lines[ln - 1]}")
                
                results.append("\n".join(result_block))
                total_matches += 1
            
            if total_matches >= max_results:
                break
        
        if not results:
            return f"未找到匹配 '{pattern}' 的内容"
        
        header = f"找到 {total_matches} 处匹配"
        if total_matches >= max_results:
            header += f"（已达到最大结果数 {max_results}）"
        header += ":"
        
        return header + "\n\n" + "\n\n".join(results)
    except ValueError as e:
        return f"错误: {str(e)}"
    except Exception as e:
        return f"错误: 搜索失败 - {str(e)}"
