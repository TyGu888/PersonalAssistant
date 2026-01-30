"""
Skill 加载器

解析 Anthropic 风格的 SKILL.md 文件，支持 frontmatter + Markdown body。
"""

import os
import re
import yaml
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """Skill 定义"""
    name: str
    description: str
    prompt: str  # Markdown body（frontmatter 之后的内容）
    metadata: dict = field(default_factory=dict)  # emoji, requires 等
    tools: list[str] = field(default_factory=list)  # 从 metadata.requires.tools 提取
    file_path: str = ""  # 源文件路径，用于调试

    def __post_init__(self):
        """初始化后处理：从 metadata 提取 tools"""
        if not self.tools and self.metadata:
            requires = self.metadata.get("requires", {})
            if isinstance(requires, dict):
                self.tools = requires.get("tools", [])


def parse_skill_file(file_path: str) -> Optional[Skill]:
    """
    解析 SKILL.md 文件
    
    文件格式：
    ```
    ---
    name: skill_name
    description: 描述
    metadata:
      emoji: "📚"
      requires:
        tools: ["tool1", "tool2"]
    ---
    
    # Markdown body
    这里是 prompt 内容...
    ```
    
    参数:
    - file_path: SKILL.md 文件路径
    
    返回:
    - Skill 对象，解析失败返回 None
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析 frontmatter 和 body
        frontmatter, body = _parse_frontmatter(content)
        
        if frontmatter is None:
            logger.warning(f"无法解析 frontmatter: {file_path}")
            return None
        
        # 从 frontmatter 提取必要字段
        name = frontmatter.get("name", "")
        if not name:
            # 如果没有指定 name，从目录名推断
            name = Path(file_path).parent.name
        
        description = frontmatter.get("description", "")
        metadata = frontmatter.get("metadata", {})
        
        # 提取 tools
        tools = []
        if metadata and isinstance(metadata, dict):
            requires = metadata.get("requires", {})
            if isinstance(requires, dict):
                tools = requires.get("tools", [])
        
        skill = Skill(
            name=name,
            description=description,
            prompt=body.strip(),
            metadata=metadata,
            tools=tools,
            file_path=file_path
        )
        
        logger.info(f"已加载 Skill: {name} (from {file_path})")
        return skill
        
    except FileNotFoundError:
        logger.error(f"Skill 文件不存在: {file_path}")
        return None
    except yaml.YAMLError as e:
        logger.error(f"Skill 文件 YAML 解析错误 {file_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"解析 Skill 文件失败 {file_path}: {e}")
        return None


def _parse_frontmatter(content: str) -> tuple[Optional[dict], str]:
    """
    分离 frontmatter 和 body
    
    参数:
    - content: 文件完整内容
    
    返回:
    - (frontmatter_dict, body_str)
    - 如果没有 frontmatter，返回 (None, content)
    """
    # 匹配 frontmatter: --- ... ---
    # 支持 YAML 风格的 frontmatter
    pattern = r'^---\s*\n(.*?)\n---\s*\n?(.*)$'
    match = re.match(pattern, content, re.DOTALL)
    
    if not match:
        # 没有 frontmatter，整个内容作为 body
        return None, content
    
    frontmatter_str = match.group(1)
    body = match.group(2)
    
    try:
        frontmatter = yaml.safe_load(frontmatter_str)
        if frontmatter is None:
            frontmatter = {}
        return frontmatter, body
    except yaml.YAMLError:
        return None, content


def load_skills(skills_dir: str = "skills") -> dict[str, Skill]:
    """
    加载所有 skills
    
    参数:
    - skills_dir: skills 目录路径
    
    返回:
    - {skill_name: Skill} 字典
    """
    skills = {}
    skills_path = Path(skills_dir)
    
    if not skills_path.exists():
        logger.warning(f"Skills 目录不存在: {skills_dir}")
        return skills
    
    if not skills_path.is_dir():
        logger.warning(f"Skills 路径不是目录: {skills_dir}")
        return skills
    
    # 遍历子目录
    for item in skills_path.iterdir():
        if not item.is_dir():
            continue
        
        # 跳过 __pycache__ 等特殊目录
        if item.name.startswith("__"):
            continue
        
        skill_file = item / "SKILL.md"
        if not skill_file.exists():
            logger.debug(f"跳过目录（无 SKILL.md）: {item}")
            continue
        
        skill = parse_skill_file(str(skill_file))
        if skill:
            skills[skill.name] = skill
    
    logger.info(f"共加载 {len(skills)} 个 Skills: {list(skills.keys())}")
    return skills


def reload_skill(skills_dir: str, skill_name: str) -> Optional[Skill]:
    """
    重新加载单个 skill（用于热加载）
    
    参数:
    - skills_dir: skills 目录路径
    - skill_name: skill 名称（目录名）
    
    返回:
    - Skill 对象，加载失败返回 None
    """
    skill_file = Path(skills_dir) / skill_name / "SKILL.md"
    if not skill_file.exists():
        logger.error(f"Skill 文件不存在: {skill_file}")
        return None
    
    return parse_skill_file(str(skill_file))
