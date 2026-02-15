import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from dateutil import parser as date_parser

from tools.registry import registry
from core.types import IncomingMessage

logger = logging.getLogger(__name__)

# 供持久化 job 使用：Gateway 启动时注入，触发时用其 publish 唤醒消息
_reminder_bus = None

# 持久化 job 必须用可序列化的函数引用（模块路径字符串），此为实际执行的回调
REMINDER_JOB_FUNC = "tools.scheduler:run_scheduled_reminder"


async def run_scheduled_reminder(
    content: Optional[str] = None,
    user_id: Optional[str] = None,
    channel: Optional[str] = None,
    auto_continue: bool = False,
):
    """定时提醒触发时由 APScheduler 调用（需通过 REMINDER_JOB_FUNC 字符串引用以支持持久化）。"""
    global _reminder_bus
    if _reminder_bus is None:
        logger.warning("run_scheduled_reminder: _reminder_bus not set, skip")
        return
    try:
        wake_msg = IncomingMessage(
            channel="system",
            user_id="system",
            text=f"[Scheduled Reminder] Please remind user {user_id} on {channel}: {content}. Use send_message tool to deliver.",
            reply_expected=False,
            raw={"target_channel": channel, "target_user": user_id},
        )
        await _reminder_bus.publish(wake_msg, wait_reply=False)
    except Exception as e:
        logger.error(f"Scheduled reminder failed: {e}", exc_info=True)


def parse_reminder_time(time_str: str) -> datetime:
    """
    解析提醒时间
    
    支持格式:
    - 'HH:MM' -> 今天的指定时间（如果已过则是明天）
    - 'YYYY-MM-DD HH:MM' -> 指定日期时间
    
    返回: datetime 对象
    """
    time_str = time_str.strip()
    now = datetime.now()
    
    # 尝试解析为完整日期时间格式
    if len(time_str) > 5:  # 可能是完整日期时间格式
        try:
            dt = date_parser.parse(time_str)
            return dt
        except (ValueError, TypeError):
            pass
    
    # 尝试解析为 HH:MM 格式
    try:
        time_parts = time_str.split(':')
        if len(time_parts) == 2:
            hour = int(time_parts[0])
            minute = int(time_parts[1])
            
            # 构造今天的指定时间
            target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # 如果时间已过，则设置为明天
            if target_time <= now:
                target_time += timedelta(days=1)
            
            return target_time
    except (ValueError, IndexError):
        pass
    
    # 如果都解析失败，尝试使用 dateutil 的通用解析
    try:
        return date_parser.parse(time_str)
    except (ValueError, TypeError):
        raise ValueError(f"无法解析时间格式: {time_str}")


@registry.register(
    name="scheduler",
    description=(
        "Manage scheduled reminders. "
        "Actions: add (set new reminder), list (show user's reminders), cancel (remove a reminder)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "cancel"],
                "description": "Action to perform"
            },
            "time": {"type": "string", "description": "Reminder time: 'HH:MM' or 'YYYY-MM-DD HH:MM' (for add)"},
            "content": {"type": "string", "description": "Reminder content (for add)"},
            "user_id": {"type": "string", "description": "User ID (for add/list)"},
            "channel": {"type": "string", "description": "Channel to deliver reminder (for add)"},
            "auto_continue": {"type": "boolean", "description": "Loop reminder (for add)"},
            "job_id": {"type": "string", "description": "Job ID to cancel (for cancel)"}
        },
        "required": ["action"]
    }
)
async def scheduler(action: str, time: str = None, content: str = None, user_id: str = None,
                    channel: str = None, auto_continue: bool = False, job_id: str = None, context=None) -> str:
    """Manage scheduled reminders."""

    if action == "add":
        return await _scheduler_add(time=time, content=content, user_id=user_id,
                                    channel=channel, auto_continue=auto_continue, context=context)
    elif action == "list":
        return await _scheduler_list(user_id=user_id, context=context)
    elif action == "cancel":
        return await _scheduler_cancel(job_id=job_id, context=context)
    else:
        return f"错误: 未知 action '{action}'。可用: add, list, cancel"


async def _scheduler_add(time: str = None, content: str = None, user_id: str = None,
                         channel: str = None, auto_continue: bool = False, context=None) -> str:
    """添加定时任务"""
    if context is None:
        return "错误: 缺少上下文信息"

    if not time or not content or not user_id or not channel:
        return "错误: add 操作需要 time, content, user_id, channel"

    sched = context.get("scheduler")
    if sched is None:
        return "错误: 无法获取 scheduler 实例"

    # 解析时间
    try:
        run_date = parse_reminder_time(time)
    except ValueError as e:
        return f"错误: {str(e)}"

    # 检查时间是否在过去
    now = datetime.now()
    if run_date <= now:
        return f"错误: 提醒时间 {run_date.strftime('%Y-%m-%d %H:%M')} 已经过去了。当前时间是 {now.strftime('%Y-%m-%d %H:%M')}，请设置一个未来的时间。"

    # 生成唯一 job_id
    jid = f"reminder_{user_id}_{uuid.uuid4().hex[:8]}"

    logger.debug(f"scheduler add: user_id={user_id}, job_id={jid}")

    try:
        sched.add_job(
            REMINDER_JOB_FUNC,
            "date",
            run_date=run_date,
            id=jid,
            kwargs={
                "content": content,
                "user_id": user_id,
                "channel": channel,
                "auto_continue": auto_continue,
            },
            replace_existing=True,
        )

        time_str = run_date.strftime("%Y-%m-%d %H:%M")
        mode = "循环提醒" if auto_continue else "单次提醒"
        return f"已设置{mode}：{time_str} - {content}"
    except Exception as e:
        return f"错误: 添加定时任务失败 - {str(e)}"


async def _scheduler_list(user_id: str = None, context=None) -> str:
    """列出定时任务"""
    if context is None:
        return "错误: 缺少上下文信息"

    if not user_id:
        return "错误: list 操作需要 user_id"

    sched = context.get("scheduler")
    if sched is None:
        return "错误: 无法获取 scheduler 实例"

    try:
        jobs = sched.get_jobs()

        user_jobs = []
        for job in jobs:
            if job.id and job.id.startswith(f"reminder_{user_id}_"):
                user_jobs.append(job)

        if not user_jobs:
            return f"用户 {user_id} 暂无定时提醒"

        lines = [f"用户 {user_id} 的定时提醒列表："]
        for i, job in enumerate(user_jobs, 1):
            run_date = job.next_run_time
            content = job.kwargs.get('content', '未知内容')
            auto_continue = job.kwargs.get('auto_continue', False)
            job_id_short = job.id.split('_')[-1] if '_' in job.id else job.id
            mode_tag = "[循环]" if auto_continue else "[单次]"

            if run_date:
                time_str = run_date.strftime("%Y-%m-%d %H:%M")
                lines.append(f"{i}. [{job_id_short}] {mode_tag} {time_str} - {content}")
            else:
                lines.append(f"{i}. [{job_id_short}] {mode_tag} 时间未设置 - {content}")

        return "\n".join(lines)
    except Exception as e:
        return f"错误: 获取任务列表失败 - {str(e)}"


async def _scheduler_cancel(job_id: str = None, context=None) -> str:
    """取消任务"""
    if context is None:
        return "错误: 缺少上下文信息"

    if not job_id:
        return "错误: cancel 操作需要 job_id"

    sched = context.get("scheduler")
    if sched is None:
        return "错误: 无法获取 scheduler 实例"

    try:
        job = sched.get_job(job_id)
        if job is None:
            return f"错误: 任务 {job_id} 不存在"

        sched.remove_job(job_id)
        return f"已取消提醒: {job_id}"
    except Exception as e:
        return f"错误: 取消任务失败 - {str(e)}"
