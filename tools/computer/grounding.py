"""
Grounding Engine - 自主 GUI 任务执行器

接收复合任务（如 "打开微信给张三发消息"），自主完成所有步骤。
主 Agent 只看到最终文本结果。

内部循环:
  截图 → VisionLLM(规划+定位) → 执行操作 → 记录 → 重复

Vision 后端可插拔:
- VisionAPIBackend: 通过 OpenAI-compatible Vision API（Qwen3VL / GPT-4o / Claude）
- 未来: ShowUI 本地模型、OmniParser、macOS Accessibility
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI

from tools.computer.actions import ActionBackend
from tools.computer.memory import ActionMemory

logger = logging.getLogger(__name__)


# ===== 数据结构 =====

@dataclass
class StepPlan:
    """VisionLLM 单步输出"""
    done: bool = False
    failed: bool = False
    reasoning: str = ""
    action_type: str = ""       # "click", "type", "hotkey", "scroll", "wait"
    coords: list = None         # [x, y] for click
    text: str = ""              # for type
    keys: str = ""              # for hotkey, e.g. "cmd+c"
    direction: str = ""         # for scroll
    amount: int = 3             # for scroll
    seconds: float = 1.0        # for wait
    fail_reason: str = ""


@dataclass
class TaskResult:
    """任务执行结果，返回给主 Agent"""
    success: bool
    description: str
    steps_taken: int = 0
    screenshot: Optional[str] = None  # 最终截图路径（失败时供主 Agent 诊断）

    def to_text(self) -> str:
        status = "成功" if self.success else "失败"
        text = f"[{status}] {self.description} (共 {self.steps_taken} 步)"
        if not self.success and self.screenshot:
            text += f"\n最终截图: {self.screenshot}"
        return text


# ===== Vision 后端接口 =====

class BaseVisionBackend(ABC):
    """Vision 后端基类。子类只需实现 plan_step 即可。"""

    @abstractmethod
    async def plan_step(
        self,
        task: str,
        screenshot_path: str,
        action_history: str,
        step: int,
    ) -> StepPlan:
        """
        核心方法: 看截图 + 理解任务 + 决定下一步 + 定位坐标

        一次调用同时完成规划和定位（效率最高）。
        未来如果用 ShowUI 做定位，可以拆分为两次调用。
        """
        ...


class VisionAPIBackend(BaseVisionBackend):
    """
    通过 OpenAI-compatible Vision API 实现规划 + 定位。

    支持: Qwen3VL / Qwen2.5VL / GPT-4o / Claude (任何支持 vision 的 OpenAI-compatible API)
    切换模型只需改 config 中的 llm_profiles + computer_use.vision_profile。
    """

    def __init__(self, api_key: str, base_url: str, model: str, **kwargs):
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=kwargs.get("timeout", 60),
            max_retries=kwargs.get("max_retries", 1),
        )

    async def plan_step(
        self,
        task: str,
        screenshot_path: str,
        action_history: str,
        step: int,
    ) -> StepPlan:
        from tools.image import process_image_for_llm
        img_data = process_image_for_llm(screenshot_path, max_dimension=1920, max_size_kb=1500)

        prompt = self._build_prompt(task, action_history, step)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": img_data["data_url"]}},
                    ],
                }],
                max_tokens=400,
            )
            raw = response.choices[0].message.content.strip()
            return self._parse_response(raw)
        except Exception as e:
            logger.error(f"VisionAPI call failed: {e}")
            return StepPlan(failed=True, fail_reason=f"VisionAPI error: {e}")

    def _build_prompt(self, task: str, action_history: str, step: int) -> str:
        return f"""You are an autonomous GUI agent. You see a screenshot and must decide the next action to complete the given task.

## Task
{task}

## Actions completed so far
{action_history}

## Current step
{step + 1}

## Instructions
Analyze the screenshot carefully. Determine the current state and decide the next action.

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "done": false,
  "failed": false,
  "reasoning": "brief explanation of current state and what to do next",
  "action_type": "click",
  "coords": [x, y],
  "text": "",
  "keys": "",
  "direction": "",
  "amount": 3,
  "seconds": 1.0,
  "fail_reason": ""
}}

Field rules:
- "done": true when the task is fully completed. Set "reasoning" to explain why.
- "failed": true when the task cannot be completed. Set "fail_reason".
- "action_type": one of "click", "type", "hotkey", "scroll", "wait"
- "coords": [x, y] pixel coordinates for "click" (logical pixels, NOT retina physical)
- "text": the text to type for "type"
- "keys": shortcut for "hotkey", e.g. "cmd+c", "enter", "escape"
- "direction": "up"/"down"/"left"/"right" for "scroll"
- "amount": scroll amount (default 3)
- "seconds": wait duration for "wait"
- Only include fields relevant to the chosen action_type.
- If an overlay/popup blocks the target, handle it first (close it, dismiss it).
- Coordinates must be precise — click the exact center of the target element."""

    def _parse_response(self, raw: str) -> StepPlan:
        """解析 VisionLLM 的 JSON 响应，容错处理"""
        # 去掉可能的 markdown code block
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # 去掉首尾 ``` 行
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse VisionLLM response as JSON: {raw[:200]}")
            return StepPlan(failed=True, fail_reason=f"Invalid JSON from VisionLLM: {raw[:100]}")

        return StepPlan(
            done=data.get("done", False),
            failed=data.get("failed", False),
            reasoning=data.get("reasoning", ""),
            action_type=data.get("action_type", ""),
            coords=data.get("coords"),
            text=data.get("text", ""),
            keys=data.get("keys", ""),
            direction=data.get("direction", ""),
            amount=data.get("amount", 3),
            seconds=data.get("seconds", 1.0),
            fail_reason=data.get("fail_reason", ""),
        )


# ===== Grounding Engine =====

class GroundingEngine:
    """
    自主 GUI 任务执行器。

    对外接口只有一个: execute_task(task) → TaskResult
    内部自主完成截图、规划、定位、操作、验证全流程。
    """

    def __init__(self, config: dict):
        cu_config = config.get("computer_use", {})

        # Action Backend
        screenshot_dir = cu_config.get("screenshot_dir", "/tmp/agent_screenshots")
        self.actions = ActionBackend(screenshot_dir=screenshot_dir)

        # Memory
        mem_config = cu_config.get("memory", {})
        self.memory = ActionMemory(
            max_screenshots=mem_config.get("max_screenshots", 2),
            max_text_history=mem_config.get("max_text_history", 50),
        )

        # Vision Backend
        self.vision = self._init_vision_backend(config)

        # 操作间隔
        self.action_wait = cu_config.get("action_wait", 0.3)

    def _init_vision_backend(self, config: dict) -> BaseVisionBackend:
        """
        根据 config 初始化 Vision 后端。

        读取 computer_use.vision_profile 指向的 llm_profiles 条目。
        切换模型只需改 vision_profile 的值。
        """
        cu_config = config.get("computer_use", {})
        profile_name = cu_config.get("vision_profile", "")
        profiles = config.get("llm_profiles", {})

        if not profile_name or profile_name not in profiles:
            raise ValueError(
                f"computer_use.vision_profile='{profile_name}' not found in llm_profiles. "
                f"Available: {list(profiles.keys())}"
            )

        profile = profiles[profile_name]
        backend_type = cu_config.get("vision_backend", "vision_api")

        if backend_type == "vision_api":
            return VisionAPIBackend(
                api_key=profile.get("api_key", ""),
                base_url=profile.get("base_url", ""),
                model=profile.get("model", ""),
                timeout=profile.get("timeout", 60),
                max_retries=profile.get("max_retries", 1),
            )
        else:
            raise ValueError(
                f"Unknown vision_backend: {backend_type}. "
                f"Currently supported: vision_api"
            )

    async def execute_task(self, task: str, max_steps: int = 15) -> TaskResult:
        """
        执行完整 GUI 任务。

        输入: "打开微信，找到张三，发送消息：明天开会"
        输出: TaskResult(success=True, description="已向张三发送消息", steps_taken=5)

        内部循环:
        1. 截图
        2. VisionLLM 分析截图 → 输出 done/fail/action
        3. 执行 action
        4. 记录到 memory
        5. 等待 UI 更新
        6. 回到 1
        """
        self.memory.reset()
        logger.info(f"[ComputerUse] Starting task: {task}")

        for step in range(max_steps):
            # 1. 截图
            screenshot_path = self.actions.screenshot()
            self.memory.push_screenshot(screenshot_path)

            # 2. VisionLLM 规划
            plan = await self.vision.plan_step(
                task=task,
                screenshot_path=screenshot_path,
                action_history=self.memory.recent_actions_text(10),
                step=step,
            )

            logger.info(
                f"[ComputerUse] Step {step+1}: "
                f"action={plan.action_type}, reasoning={plan.reasoning[:80]}"
            )

            # 3. 完成？
            if plan.done:
                desc = plan.reasoning or "任务完成"
                self.memory.record_action("done", desc, "success")
                logger.info(f"[ComputerUse] Task completed in {step+1} steps: {desc}")
                return TaskResult(
                    success=True,
                    description=desc,
                    steps_taken=step + 1,
                    screenshot=screenshot_path,
                )

            # 4. 失败？
            if plan.failed:
                reason = plan.fail_reason or "无法完成任务"
                self.memory.record_action("fail", reason, "failed")
                logger.warning(f"[ComputerUse] Task failed at step {step+1}: {reason}")
                return TaskResult(
                    success=False,
                    description=reason,
                    steps_taken=step + 1,
                    screenshot=screenshot_path,
                )

            # 5. 执行操作
            result_text = await self._execute_action(plan)
            self.memory.record_action(plan.action_type, plan.reasoning, result_text)

            # 6. 等待 UI 更新
            await asyncio.sleep(self.action_wait)

        # 超过最大步数
        final_screenshot = self.actions.screenshot()
        logger.warning(f"[ComputerUse] Task exceeded {max_steps} steps: {task}")
        return TaskResult(
            success=False,
            description=f"任务在 {max_steps} 步内未完成",
            steps_taken=max_steps,
            screenshot=final_screenshot,
        )

    async def _execute_action(self, plan: StepPlan) -> str:
        """根据 plan 执行具体操作"""
        try:
            if plan.action_type == "click":
                if not plan.coords or len(plan.coords) < 2:
                    return "FAILED: no coordinates for click"
                x, y = int(plan.coords[0]), int(plan.coords[1])
                self.actions.click(x, y)
                return f"clicked ({x}, {y})"

            elif plan.action_type == "type":
                if not plan.text:
                    return "FAILED: no text for type"
                self.actions.type_text(plan.text)
                return f"typed '{plan.text[:30]}'"

            elif plan.action_type == "hotkey":
                if not plan.keys:
                    return "FAILED: no keys for hotkey"
                key_list = [k.strip() for k in plan.keys.split("+")]
                self.actions.hotkey(*key_list)
                return f"pressed {plan.keys}"

            elif plan.action_type == "scroll":
                direction = plan.direction or "down"
                amount = plan.amount or 3
                self.actions.scroll(direction, amount)
                return f"scrolled {direction} {amount}"

            elif plan.action_type == "wait":
                wait_time = min(plan.seconds or 1.0, 10.0)  # 最大等 10s
                await asyncio.sleep(wait_time)
                return f"waited {wait_time}s"

            else:
                return f"unknown action_type: {plan.action_type}"

        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return f"FAILED: {e}"
