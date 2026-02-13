"""
Action Memory - GUI 操作记忆管理

四层记忆结构 (参考 UFO + ShowUI):
1. Working Screenshots: 滑动窗口，只保留最近 N 张（默认 2）
2. Action History: 纯文本动作记录，极轻量，全部保留
3. Key Snapshots: 重要状态截图，由 engine 标记保存
4. Experience: 任务完成后压缩为经验记录，存入长期记忆
"""

import os
import time
import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ActionRecord:
    """单次操作记录"""
    step: int
    action_type: str       # "click", "type", "scroll", "hotkey", "wait", "done", "fail"
    description: str       # 操作描述
    result: str            # 执行结果
    timestamp: float = field(default_factory=time.time)


class ActionMemory:
    """
    分层记忆管理。

    截图是 token 杀手（~1000-2000 tokens/张），严格控制数量。
    文本历史极轻量（~10 tokens/条），可以大量保留。
    """

    def __init__(self, max_screenshots: int = 2, max_text_history: int = 50):
        self._recent_screenshots: deque[str] = deque(maxlen=max_screenshots)
        self._action_history: list[ActionRecord] = []
        self._max_text_history = max_text_history
        self._step_counter = 0
        self._key_snapshots: dict[str, str] = {}  # name → path

    def push_screenshot(self, path: str):
        """添加截图到滑动窗口，旧截图自动删除。"""
        if len(self._recent_screenshots) == self._recent_screenshots.maxlen:
            oldest = self._recent_screenshots[0]
            if oldest not in self._key_snapshots.values():
                try:
                    os.remove(oldest)
                except OSError:
                    pass
        self._recent_screenshots.append(path)

    def record_action(self, action_type: str, description: str, result: str):
        """记录一次操作（纯文本，极轻量）"""
        self._step_counter += 1
        self._action_history.append(ActionRecord(
            step=self._step_counter,
            action_type=action_type,
            description=description,
            result=result,
        ))
        if len(self._action_history) > self._max_text_history:
            self._action_history = self._action_history[-self._max_text_history:]

    def save_key_snapshot(self, name: str, screenshot_path: str):
        """保存关键快照（如: app_opened, error_dialog, task_complete）"""
        self._key_snapshots[name] = screenshot_path

    def recent_screenshots(self) -> list[str]:
        return list(self._recent_screenshots)

    def latest_screenshot(self) -> str | None:
        return self._recent_screenshots[-1] if self._recent_screenshots else None

    def recent_actions_text(self, n: int = 10) -> str:
        """获取最近 N 步操作的文本描述"""
        recent = self._action_history[-n:]
        if not recent:
            return "(no previous actions)"
        return "\n".join(
            f"Step {r.step}: [{r.action_type}] {r.description} → {r.result}"
            for r in recent
        )

    @property
    def step_count(self) -> int:
        return self._step_counter

    def get_experience_record(self, task: str, success: bool) -> dict:
        """生成经验记录，可存入长期记忆供 RAG 检索"""
        return {
            "task": task,
            "success": success,
            "steps": [
                {"action": f"[{r.action_type}] {r.description}", "result": r.result}
                for r in self._action_history
            ],
            "total_steps": self._step_counter,
            "timestamp": time.time(),
        }

    def reset(self):
        """任务开始前重置"""
        # 清理滑动窗口截图
        for path in self._recent_screenshots:
            if path not in self._key_snapshots.values():
                try:
                    os.remove(path)
                except OSError:
                    pass
        self._recent_screenshots.clear()
        self._action_history.clear()
        self._step_counter = 0
        self._key_snapshots.clear()
