"""
Sub-Agent Tools - 子 Agent 管理工具

提供:
- agent_spawn: 生成子 Agent 执行任务
- agent_list: 列出子 Agent 状态
- agent_send: 给子 Agent 发消息
- agent_history: 获取子 Agent 对话历史
"""

from tools.registry import registry
from core.types import IncomingMessage
import logging
import uuid
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ===== 数据结构 =====

@dataclass
class SubAgentRun:
    """子 Agent 运行实例"""
    run_id: str
    parent_session: str
    child_session: str
    task: str
    label: str
    agent_id: str
    status: str  # "pending" | "running" | "completed" | "failed" | "timeout"
    created_at: datetime
    completed_at: Optional[datetime] = None
    result: Optional[str] = None
    error: Optional[str] = None
    _task: Optional[asyncio.Task] = field(default=None, repr=False)


class SubAgentRegistry:
    """子 Agent 注册表"""
    
    def __init__(self):
        self._runs: Dict[str, SubAgentRun] = {}
        self._lock = asyncio.Lock()
    
    async def register(self, run: SubAgentRun):
        """注册子 Agent 运行"""
        async with self._lock:
            self._runs[run.run_id] = run
    
    def get(self, run_id: str) -> Optional[SubAgentRun]:
        """获取子 Agent 运行信息"""
        return self._runs.get(run_id)
    
    def list_by_parent(self, parent_session: str) -> list[SubAgentRun]:
        """列出指定父 session 的所有子 Agent"""
        return [r for r in self._runs.values() if r.parent_session == parent_session]
    
    async def update_status(
        self, 
        run_id: str, 
        status: str, 
        result: str = None, 
        error: str = None
    ):
        """更新子 Agent 状态"""
        async with self._lock:
            if run_id in self._runs:
                run = self._runs[run_id]
                run.status = status
                run.completed_at = datetime.now()
                if result is not None:
                    run.result = result
                if error is not None:
                    run.error = error


# 全局注册表
_subagent_registry = SubAgentRegistry()


# ===== Tool 实现 =====

@registry.register(
    name="agent_spawn",
    description="生成子 Agent 执行复杂任务。子 Agent 独立运行，完成后会报告结果。适用于需要长时间执行或独立思考的任务。",
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string", 
                "description": "任务描述（会作为消息发送给子 Agent）"
            },
            "label": {
                "type": "string", 
                "description": "任务标签（便于追踪）",
                "default": ""
            },
            "agent_id": {
                "type": "string", 
                "description": "使用哪个 Agent 模板",
                "default": "default"
            },
            "timeout_seconds": {
                "type": "integer", 
                "description": "超时时间（秒）",
                "default": 300
            },
            "wait": {
                "type": "boolean", 
                "description": "是否等待完成（True=同步等待结果，False=后台运行）",
                "default": False
            }
        },
        "required": ["task"]
    }
)
async def agent_spawn(
    task: str, 
    label: str = "", 
    agent_id: str = "default",
    timeout_seconds: int = 300, 
    wait: bool = False, 
    context=None
) -> str:
    """
    生成子 Agent 执行任务
    
    参数:
    - task: 任务描述
    - label: 任务标签（可选）
    - agent_id: Agent 模板 ID
    - timeout_seconds: 超时时间
    - wait: 是否同步等待结果
    - context: 执行上下文（由 Engine 注入）
    
    返回:
    - wait=True: 返回任务执行结果
    - wait=False: 返回 run_id，可用于后续查询
    """
    if not context:
        return "错误: 缺少执行上下文"
    
    engine = context.get("engine")
    if not engine:
        return "错误: 缺少 engine 引用"
    
    # 从 context 获取当前 session 信息
    # 注意: context 中需要有 msg_context 来获取当前会话信息
    msg_context = context.get("msg_context", {})
    channel = msg_context.get("channel", "subagent")
    user_id = msg_context.get("user_id", "system")
    parent_session = msg_context.get("session_id", f"{channel}:dm:{user_id}")
    
    # 生成子 session_id
    run_id = str(uuid.uuid4())[:8]
    child_session = f"subagent:{parent_session}:{run_id}"
    
    # 创建 SubAgentRun
    run = SubAgentRun(
        run_id=run_id,
        parent_session=parent_session,
        child_session=child_session,
        task=task,
        label=label or f"task-{run_id}",
        agent_id=agent_id,
        status="pending",
        created_at=datetime.now()
    )
    
    await _subagent_registry.register(run)
    
    # 构造子 Agent 的 IncomingMessage
    # 添加系统提示说明这是子任务
    sub_task_prompt = f"""[子任务执行模式]
你正在作为子 Agent 执行一个独立任务。完成后请直接返回结果。

任务: {task}"""
    
    sub_message = IncomingMessage(
        channel="subagent",
        user_id=f"subagent:{run_id}",
        text=sub_task_prompt,
        raw={
            "parent_session": parent_session,
            "run_id": run_id,
            "is_subagent": True
        }
    )
    
    async def execute_subagent():
        """执行子 Agent 任务"""
        try:
            await _subagent_registry.update_status(run_id, "running")
            
            # 调用 engine.handle() 执行任务
            response = await asyncio.wait_for(
                engine.handle(sub_message),
                timeout=timeout_seconds
            )
            
            result_text = response.text if response else "无响应"
            await _subagent_registry.update_status(run_id, "completed", result=result_text)
            return result_text
            
        except asyncio.TimeoutError:
            error_msg = f"任务超时（{timeout_seconds}秒）"
            await _subagent_registry.update_status(run_id, "timeout", error=error_msg)
            logger.warning(f"SubAgent {run_id} timed out: {task[:50]}...")
            return error_msg
            
        except Exception as e:
            error_msg = str(e)
            await _subagent_registry.update_status(run_id, "failed", error=error_msg)
            logger.error(f"SubAgent {run_id} failed: {e}", exc_info=True)
            return f"执行失败: {error_msg}"
    
    if wait:
        # 同步等待结果
        result = await execute_subagent()
        return f"[子任务完成] run_id={run_id}\n\n{result}"
    else:
        # 后台运行
        task_obj = asyncio.create_task(execute_subagent())
        run._task = task_obj
        logger.info(f"SubAgent spawned: run_id={run_id}, task={task[:50]}...")
        return f"子 Agent 已启动: run_id={run_id}, label={run.label}\n使用 agent_list 查看状态，agent_history 获取结果。"


@registry.register(
    name="agent_list",
    description="列出当前会话的子 Agent 状态",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    }
)
async def agent_list(context=None) -> str:
    """
    列出当前会话的所有子 Agent 状态
    
    返回格式化的状态列表
    """
    if not context:
        return "错误: 缺少执行上下文"
    
    msg_context = context.get("msg_context", {})
    channel = msg_context.get("channel", "subagent")
    user_id = msg_context.get("user_id", "system")
    parent_session = msg_context.get("session_id", f"{channel}:dm:{user_id}")
    
    runs = _subagent_registry.list_by_parent(parent_session)
    
    if not runs:
        return "当前没有子 Agent 任务。"
    
    # 按创建时间排序
    runs.sort(key=lambda r: r.created_at, reverse=True)
    
    lines = ["子 Agent 任务列表:", ""]
    
    status_icons = {
        "pending": "⏳",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "timeout": "⏰"
    }
    
    for run in runs:
        icon = status_icons.get(run.status, "❓")
        created = run.created_at.strftime("%H:%M:%S")
        
        line = f"{icon} [{run.run_id}] {run.label}"
        line += f" | 状态: {run.status}"
        line += f" | 创建: {created}"
        
        if run.completed_at:
            duration = (run.completed_at - run.created_at).total_seconds()
            line += f" | 耗时: {duration:.1f}s"
        
        lines.append(line)
        
        # 显示任务摘要
        task_summary = run.task[:60] + "..." if len(run.task) > 60 else run.task
        lines.append(f"   任务: {task_summary}")
        
        # 如果已完成，显示结果摘要
        if run.status == "completed" and run.result:
            result_summary = run.result[:80] + "..." if len(run.result) > 80 else run.result
            lines.append(f"   结果: {result_summary}")
        elif run.error:
            lines.append(f"   错误: {run.error}")
        
        lines.append("")
    
    return "\n".join(lines)


@registry.register(
    name="agent_send",
    description="给子 Agent 发送消息（用于正在运行的子 Agent 进行交互）",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string", 
                "description": "子 Agent 的 run_id"
            },
            "message": {
                "type": "string", 
                "description": "要发送的消息"
            }
        },
        "required": ["run_id", "message"]
    }
)
async def agent_send(run_id: str, message: str, context=None) -> str:
    """
    给子 Agent 发送消息
    
    参数:
    - run_id: 子 Agent 的运行 ID
    - message: 要发送的消息
    
    返回: 子 Agent 的响应
    """
    if not context:
        return "错误: 缺少执行上下文"
    
    engine = context.get("engine")
    if not engine:
        return "错误: 缺少 engine 引用"
    
    run = _subagent_registry.get(run_id)
    if not run:
        return f"错误: 找不到子 Agent run_id={run_id}"
    
    if run.status not in ("pending", "running", "completed"):
        return f"错误: 子 Agent 状态为 {run.status}，无法发送消息"
    
    # 构造消息发送给子 Agent 的 session
    sub_message = IncomingMessage(
        channel="subagent",
        user_id=f"subagent:{run_id}",
        text=message,
        raw={
            "parent_session": run.parent_session,
            "run_id": run_id,
            "is_subagent": True,
            "is_followup": True
        }
    )
    
    try:
        response = await engine.handle(sub_message)
        return f"[子 Agent {run_id} 响应]\n{response.text}"
    except Exception as e:
        logger.error(f"Failed to send message to SubAgent {run_id}: {e}", exc_info=True)
        return f"发送失败: {e}"


@registry.register(
    name="agent_history",
    description="获取子 Agent 的对话历史",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string", 
                "description": "子 Agent 的 run_id"
            },
            "limit": {
                "type": "integer", 
                "description": "返回消息数量",
                "default": 10
            }
        },
        "required": ["run_id"]
    }
)
async def agent_history(run_id: str, limit: int = 10, context=None) -> str:
    """
    获取子 Agent 的对话历史
    
    参数:
    - run_id: 子 Agent 的运行 ID
    - limit: 返回的消息数量限制
    
    返回: 格式化的对话历史
    """
    if not context:
        return "错误: 缺少执行上下文"
    
    memory = context.get("memory")
    if not memory:
        return "错误: 缺少 memory 引用"
    
    run = _subagent_registry.get(run_id)
    if not run:
        return f"错误: 找不到子 Agent run_id={run_id}"
    
    # 从 memory 获取子 Agent 的对话历史
    history = memory.get_history(run.child_session, limit=limit)
    
    if not history:
        return f"子 Agent {run_id} 暂无对话历史。"
    
    lines = [
        f"子 Agent [{run_id}] 对话历史:",
        f"状态: {run.status} | 任务: {run.label}",
        "-" * 40
    ]
    
    for msg in history:
        role = "🧑 用户" if msg["role"] == "user" else "🤖 助手"
        content = msg["content"]
        # 截断过长的内容
        if len(content) > 500:
            content = content[:500] + "...(已截断)"
        lines.append(f"\n{role}:")
        lines.append(content)
    
    return "\n".join(lines)


# ===== 辅助函数 =====

def get_subagent_registry() -> SubAgentRegistry:
    """获取全局子 Agent 注册表（供外部模块使用）"""
    return _subagent_registry
