from core.types import IncomingMessage, Route
import re
from typing import Optional


class Router:
    def __init__(self, rules: list[dict]):
        """
        rules 格式:
        [
            {
                "match": {"channel": "telegram", "pattern": "学习.*"},
                "agent": "study_coach",
                "tools": ["scheduler_add", "scheduler_list"]
            }
        ]
        
        match 可以包含:
        - channel: 频道名称（可选）
        - user_id: 用户ID（可选）
        - pattern: 正则表达式匹配消息文本（可选）
        - 空的 match {} 表示兜底规则
        """
        self.rules = rules
    
    def resolve(self, msg: IncomingMessage) -> Route:
        """
        输入: IncomingMessage
        输出: Route(agent_id, tools)
        
        匹配逻辑:
        1. 遍历 rules
        2. 检查 channel 是否匹配（如果指定）
        3. 检查 user_id 是否匹配（如果指定）
        4. 检查 text 是否匹配 pattern（正则，如果指定）
        5. 全部通过则返回该 Route
        6. 无匹配则返回默认 Route("default", [])
        """
        for rule in self.rules:
            match_conditions = rule.get("match", {})
            
            # 如果 match 为空，表示兜底规则，直接匹配
            if not match_conditions:
                return Route(
                    agent_id=rule.get("agent", "default"),
                    tools=rule.get("tools", [])
                )
            
            # 检查 channel 是否匹配（如果指定）
            if "channel" in match_conditions:
                if msg.channel != match_conditions["channel"]:
                    continue
            
            # 检查 user_id 是否匹配（如果指定）
            if "user_id" in match_conditions:
                if msg.user_id != match_conditions["user_id"]:
                    continue
            
            # 检查 text 是否匹配 pattern（正则，如果指定）
            if "pattern" in match_conditions:
                pattern = match_conditions["pattern"]
                if not re.search(pattern, msg.text):
                    continue
            
            # 所有条件都通过，返回该 Route
            return Route(
                agent_id=rule.get("agent", "default"),
                tools=rule.get("tools", [])
            )
        
        # 无匹配则返回默认 Route
        return Route(agent_id="default", tools=[])
