from agents.base import BaseAgent


class StudyCoachAgent(BaseAgent):
    """
    学习教练 Agent
    
    一个严厉但关心学生的学习教练，督促用户学习，提醒完成任务。
    """
    
    DEFAULT_PROMPT = """你是一个严厉但关心学生的学习教练。
你的职责是督促用户学习，提醒他们完成任务，并在他们懈怠时给予警告。
语气要直接、坚定，但也要有适度的鼓励。

你可以使用的工具:
- scheduler_add: 设置定时提醒
- scheduler_list: 查看当前提醒
- scheduler_cancel: 取消提醒

当用户说要学习某个东西时，主动问是否需要设置提醒。
当用户完成学习任务时，给予肯定和鼓励。
当用户拖延或找借口时，适度施压并提醒他们的目标。

记住：你的目标是帮助用户养成良好的学习习惯，保持学习动力。"""
    
    def __init__(self, llm_config: dict, custom_prompt: str = None):
        """
        初始化学习教练 Agent
        
        参数:
        - llm_config: {"api_key": "...", "base_url": "...", "model": "..."}
        - custom_prompt: 自定义提示词（可选，默认使用 DEFAULT_PROMPT）
        """
        prompt = custom_prompt or self.DEFAULT_PROMPT
        super().__init__("study_coach", prompt, llm_config)


class DefaultAgent(BaseAgent):
    """默认 Agent - 友好的通用助手"""
    
    DEFAULT_PROMPT = """你是一个友好、乐于助人的 AI 助手。
你可以帮助用户解答问题、聊天、提供建议。
保持礼貌、耐心，用简洁清晰的语言回复。"""
    
    def __init__(self, llm_config: dict, custom_prompt: str = None):
        """
        初始化默认 Agent
        
        参数:
        - llm_config: {"api_key": "...", "base_url": "...", "model": "..."}
        - custom_prompt: 自定义提示词（可选，默认使用 DEFAULT_PROMPT）
        """
        prompt = custom_prompt or self.DEFAULT_PROMPT
        super().__init__("default", prompt, llm_config)
