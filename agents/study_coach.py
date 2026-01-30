"""
Agent 定义

注意：Agent 的核心 prompt 已迁移到 skills/ 目录下的 SKILL.md 文件中。
这里保留 DEFAULT_PROMPT 作为后备，以保持向后兼容。

Skill 文件位置：
- skills/study_coach/SKILL.md
- skills/default/SKILL.md
- skills/coding_assistant/SKILL.md
"""

from agents.base import BaseAgent


class StudyCoachAgent(BaseAgent):
    """
    学习教练 Agent
    
    核心 prompt 定义在 skills/study_coach/SKILL.md
    """
    
    # 后备 prompt（当 SKILL.md 不存在时使用）
    DEFAULT_PROMPT = """你是一个严厉但关心学生的学习教练。
你的职责是督促用户学习，提醒他们完成任务，并在他们懈怠时给予警告。
语气要直接、坚定，但也要有适度的鼓励。"""
    
    def __init__(self, llm_config: dict, custom_prompt: str = None):
        """
        初始化学习教练 Agent
        
        参数:
        - llm_config: {"api_key": "...", "base_url": "...", "model": "..."}
        - custom_prompt: 自定义提示词（优先使用 skills/study_coach/SKILL.md）
        """
        prompt = custom_prompt or self.DEFAULT_PROMPT
        super().__init__("study_coach", prompt, llm_config)


class DefaultAgent(BaseAgent):
    """
    默认 Agent - 友好的通用助手
    
    核心 prompt 定义在 skills/default/SKILL.md
    """
    
    # 后备 prompt（当 SKILL.md 不存在时使用）
    DEFAULT_PROMPT = """你是一个友好、乐于助人的 AI 助手。
你可以帮助用户解答问题、聊天、提供建议。
保持礼貌、耐心，用简洁清晰的语言回复。"""
    
    def __init__(self, llm_config: dict, custom_prompt: str = None):
        """
        初始化默认 Agent
        
        参数:
        - llm_config: {"api_key": "...", "base_url": "...", "model": "..."}
        - custom_prompt: 自定义提示词（优先使用 skills/default/SKILL.md）
        """
        prompt = custom_prompt or self.DEFAULT_PROMPT
        super().__init__("default", prompt, llm_config)
