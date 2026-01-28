from tools.registry import registry
from datetime import datetime, timedelta
from dateutil import parser as date_parser
import uuid
from typing import Optional


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
    name="scheduler_add",
    description="添加定时提醒",
    parameters={
        "type": "object",
        "properties": {
            "time": {
                "type": "string",
                "description": "提醒时间。格式: 'HH:MM'(今天) 或 'YYYY-MM-DD HH:MM'"
            },
            "content": {
                "type": "string", 
                "description": "提醒内容"
            },
            "user_id": {
                "type": "string",
                "description": "提醒谁（用户ID）"
            },
            "channel": {
                "type": "string",
                "description": "通过哪个渠道提醒"
            }
        },
        "required": ["time", "content", "user_id", "channel"]
    }
)
async def scheduler_add(time: str, content: str, user_id: str, channel: str, context=None) -> str:
    """
    添加定时任务（使用注入的 context）
    
    context 包含:
    - engine: Engine 实例（用于 send_push）
    - scheduler: APScheduler 实例
    
    流程:
    1. 解析时间字符串（支持 'HH:MM' 和 'YYYY-MM-DD HH:MM'）
    2. 创建 job callback（调用 engine.send_push）
    3. 使用 scheduler.add_job 添加任务
    
    返回: "已设置提醒：2026-01-28 10:00 - 复习 GRPO"
    """
    if context is None:
        return "错误: 缺少上下文信息"
    
    engine = context.get("engine")
    scheduler = context.get("scheduler")
    
    if engine is None:
        return "错误: 无法获取 engine 实例"
    if scheduler is None:
        return "错误: 无法获取 scheduler 实例"
    
    # 解析时间
    try:
        run_date = parse_reminder_time(time)
    except ValueError as e:
        return f"错误: {str(e)}"
    
    # 生成唯一 job_id
    job_id = f"reminder_{user_id}_{uuid.uuid4().hex[:8]}"
    
    # 创建异步回调函数
    async def job_callback():
        try:
            await engine.send_push(channel, user_id, f"⏰ 提醒: {content}")
        except Exception as e:
            # 记录错误，但不抛出异常（避免影响 scheduler）
            print(f"提醒发送失败: {e}")
    
    # 添加任务到 scheduler，将元数据存储在 kwargs 中
    try:
        scheduler.add_job(
            job_callback,
            'date',
            run_date=run_date,
            id=job_id,
            kwargs={
                'content': content,
                'user_id': user_id,
                'channel': channel
            },
            replace_existing=True
        )
        
        # 格式化返回消息
        time_str = run_date.strftime("%Y-%m-%d %H:%M")
        return f"已设置提醒：{time_str} - {content}"
    except Exception as e:
        return f"错误: 添加定时任务失败 - {str(e)}"


@registry.register(
    name="scheduler_list",
    description="列出用户的所有定时提醒",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "用户ID"}
        },
        "required": ["user_id"]
    }
)
async def scheduler_list(user_id: str, context=None) -> str:
    """
    列出定时任务
    
    流程:
    1. 从 scheduler 获取所有 jobs
    2. 过滤出该用户的任务（通过 job.id 或 job.args）
    3. 格式化输出
    
    返回: 任务列表文本
    """
    if context is None:
        return "错误: 缺少上下文信息"
    
    scheduler = context.get("scheduler")
    if scheduler is None:
        return "错误: 无法获取 scheduler 实例"
    
    try:
        # 获取所有任务
        jobs = scheduler.get_jobs()
        
        # 过滤出该用户的任务（job_id 格式为 reminder_{user_id}_{uuid}）
        user_jobs = []
        for job in jobs:
            if job.id and job.id.startswith(f"reminder_{user_id}_"):
                user_jobs.append(job)
        
        if not user_jobs:
            return f"用户 {user_id} 暂无定时提醒"
        
        # 格式化输出
        lines = [f"用户 {user_id} 的定时提醒列表："]
        for i, job in enumerate(user_jobs, 1):
            run_date = job.next_run_time
            if run_date:
                time_str = run_date.strftime("%Y-%m-%d %H:%M")
                # 从 job 的 kwargs 中获取内容
                content = job.kwargs.get('content', '未知内容')
                job_id_short = job.id.split('_')[-1] if '_' in job.id else job.id
                lines.append(f"{i}. [{job_id_short}] {time_str} - {content}")
            else:
                content = job.kwargs.get('content', '未知内容')
                job_id_short = job.id.split('_')[-1] if '_' in job.id else job.id
                lines.append(f"{i}. [{job_id_short}] 时间未设置 - {content}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"错误: 获取任务列表失败 - {str(e)}"


@registry.register(
    name="scheduler_cancel",
    description="取消定时提醒",
    parameters={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "任务ID"}
        },
        "required": ["job_id"]
    }
)
async def scheduler_cancel(job_id: str, context=None) -> str:
    """
    取消任务
    
    返回: "已取消提醒: {job_id}"
    """
    if context is None:
        return "错误: 缺少上下文信息"
    
    scheduler = context.get("scheduler")
    if scheduler is None:
        return "错误: 无法获取 scheduler 实例"
    
    try:
        # 检查任务是否存在
        job = scheduler.get_job(job_id)
        if job is None:
            return f"错误: 任务 {job_id} 不存在"
        
        # 移除任务
        scheduler.remove_job(job_id)
        return f"已取消提醒: {job_id}"
    except Exception as e:
        return f"错误: 取消任务失败 - {str(e)}"
