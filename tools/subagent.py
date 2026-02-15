"""
Sub-Agent Tools - Dynamically spawn and manage sub-agents

The main Agent defines everything about a sub-agent at spawn time:
prompt, tools, model (llm_profile), max_iterations, timeout, etc.

Tools:
- agent_spawn:   Spawn a sub-agent (foreground blocking or background async)
- agent_list:    List sub-agents for the current session
- agent_query:   Get detailed status / result of a specific sub-agent
- agent_send:    Send a message to a running sub-agent (note: autonomous, no mid-exec injection)
- agent_stop:    Cancel a running background sub-agent
- agent_history: Read a sub-agent's conversation history from memory
"""

from tools.registry import registry
import logging
import uuid
import asyncio
from datetime import datetime
from typing import Optional, Dict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ===== Data structures =====

@dataclass
class SubAgentRun:
    """A single sub-agent execution record."""
    run_id: str
    parent_session: str
    child_session: str
    task: str
    label: str
    status: str  # "pending" | "running" | "completed" | "failed" | "timeout" | "cancelled"
    created_at: datetime
    prompt: str                          # system prompt used
    tools: list[str]                     # tool names used
    llm_profile: Optional[str] = None   # profile used (None = main agent's)
    completed_at: Optional[datetime] = None
    result: Optional[str] = None
    error: Optional[str] = None
    _task: Optional[asyncio.Task] = field(default=None, repr=False)


class SubAgentRegistry:
    """Thread-safe registry of sub-agent runs (asyncio.Lock)."""

    def __init__(self):
        self._runs: Dict[str, SubAgentRun] = {}
        self._lock = asyncio.Lock()

    async def register(self, run: SubAgentRun):
        async with self._lock:
            self._runs[run.run_id] = run

    def get(self, run_id: str) -> Optional[SubAgentRun]:
        return self._runs.get(run_id)

    def list_by_parent(self, parent_session: str) -> list[SubAgentRun]:
        return [r for r in self._runs.values() if r.parent_session == parent_session]

    async def update_status(
        self,
        run_id: str,
        status: str,
        result: str = None,
        error: str = None,
    ):
        async with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return
            run.status = status
            if status in ("completed", "failed", "timeout", "cancelled"):
                run.completed_at = datetime.now()
            if result is not None:
                run.result = result
            if error is not None:
                run.error = error

    async def stop(self, run_id: str) -> Optional[str]:
        """Cancel a running background sub-agent. Returns error string or None."""
        async with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return f"Sub-agent run_id={run_id} not found"
            if run.status != "running":
                return f"Sub-agent {run_id} is not running (status={run.status})"
            if run._task is None or run._task.done():
                return f"Sub-agent {run_id} has no active asyncio task"
            run._task.cancel()
            run.status = "cancelled"
            run.completed_at = datetime.now()
            run.error = "Cancelled by user"
            return None


# Global registry
_registry = SubAgentRegistry()


# ===== Helpers =====

def _get_default_tool_names(agent_loop) -> list[str]:
    """Resolve the default route's tool names (same tools the main agent would get)."""
    from core.types import IncomingMessage
    dummy = IncomingMessage(channel="subagent", user_id="sys", text="")
    route = agent_loop.router.resolve(dummy)
    return list(route.tools) if route.tools else []


def _build_llm_config(agent_loop, profile_name: str, max_iterations: int) -> dict:
    """Build an llm_config dict from a named profile."""
    profiles = agent_loop.config.get("llm_profiles", {})
    profile = profiles[profile_name]
    llm_cfg = agent_loop.config.get("llm", {})
    agent_cfg = agent_loop.config.get("agent", {})

    return {
        "api_key": profile.get("api_key"),
        "base_url": profile.get("base_url"),
        "model": profile.get("model", "gpt-4o"),
        "extra_params": profile.get("extra_params", {}),
        "features": profile.get("features", {}),
        "max_context_tokens": llm_cfg.get("max_context_tokens", 8000),
        "max_response_tokens": llm_cfg.get("max_response_tokens"),
        "max_iterations": max_iterations,
        "llm_call_timeout": profile.get("llm_call_timeout") or agent_cfg.get("llm_call_timeout", 120),
        "llm_http_timeout": (
            profile.get("llm_http_timeout")
            or profile.get("timeout")
            or agent_cfg.get("llm_http_timeout")
            or profile.get("llm_call_timeout")
            or agent_cfg.get("llm_call_timeout", 120)
        ),
        "llm_max_retries": profile.get("llm_max_retries") or agent_cfg.get("llm_max_retries", 2),
    }


def _build_tool_context(agent_loop, person_id: str, child_session: str, msg_context: dict) -> dict:
    """Build tool_context the same way AgentLoop._handle_envelope does."""
    tool_context = agent_loop.runtime.get_tool_context(person_id, child_session, msg_context)
    tool_context["dispatcher"] = agent_loop.dispatcher
    if agent_loop._scheduler:
        tool_context["scheduler"] = agent_loop._scheduler
    tool_context["bus"] = agent_loop.bus
    if agent_loop._channel_manager:
        tool_context["channel_manager"] = agent_loop._channel_manager
    tool_context["agent_loop"] = agent_loop
    return tool_context


# ===== Merged Tool =====

@registry.register(
    name="agent",
    description=(
        "Manage sub-agents for delegating tasks. "
        "Actions: spawn (create new sub-agent), list (show all sub-agents), query (get status/result by run_id), "
        "send (send message to running sub-agent), stop (cancel a sub-agent), history (read conversation history)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["spawn", "list", "query", "send", "stop", "history"],
                "description": "Action to perform"
            },
            "task": {"type": "string", "description": "Task description (for spawn)"},
            "prompt": {"type": "string", "description": "System prompt for sub-agent (for spawn, optional)"},
            "tools": {"type": "array", "items": {"type": "string"}, "description": "Tool names (for spawn, optional)"},
            "llm_profile": {"type": "string", "description": "LLM profile name (for spawn, optional)"},
            "max_iterations": {"type": "integer", "description": "Max iterations (for spawn, default 30)"},
            "timeout_seconds": {"type": "integer", "description": "Timeout (for spawn, default 300)"},
            "background": {"type": "boolean", "description": "Run in background (for spawn, default false)"},
            "label": {"type": "string", "description": "Human-readable label (for spawn)"},
            "run_id": {"type": "string", "description": "Sub-agent run ID (for query/send/stop/history)"},
            "message": {"type": "string", "description": "Message to send (for send)"},
            "limit": {"type": "integer", "description": "Max messages (for history, default 10)"}
        },
        "required": ["action"]
    }
)
async def agent(
    action: str,
    task: str = None,
    prompt: str = None,
    tools: list[str] = None,
    llm_profile: str = None,
    max_iterations: int = 30,
    timeout_seconds: int = 300,
    background: bool = False,
    label: str = None,
    run_id: str = None,
    message: str = None,
    limit: int = 10,
    context=None,
) -> str:
    """Manage sub-agents for delegating tasks."""

    if action == "spawn":
        return await _agent_spawn(
            task=task, prompt=prompt, tools=tools, llm_profile=llm_profile,
            max_iterations=max_iterations, timeout_seconds=timeout_seconds,
            background=background, label=label, context=context,
        )
    elif action == "list":
        return await _agent_list(context=context)
    elif action == "query":
        if not run_id:
            return "Error: query action requires run_id"
        return await _agent_query(run_id=run_id, context=context)
    elif action == "send":
        if not run_id:
            return "Error: send action requires run_id"
        if not message:
            return "Error: send action requires message"
        return await _agent_send(run_id=run_id, message=message, context=context)
    elif action == "stop":
        if not run_id:
            return "Error: stop action requires run_id"
        return await _agent_stop(run_id=run_id, context=context)
    elif action == "history":
        if not run_id:
            return "Error: history action requires run_id"
        return await _agent_history(run_id=run_id, limit=limit, context=context)
    else:
        return f"Error: unknown action '{action}'. Available: spawn, list, query, send, stop, history"


async def _agent_spawn(
    task: str = None,
    prompt: str = None,
    tools: list[str] = None,
    llm_profile: str = None,
    max_iterations: int = 30,
    timeout_seconds: int = 300,
    background: bool = False,
    label: str = None,
    context=None,
) -> str:
    if not context:
        return "Error: missing execution context"

    if not task:
        return "Error: spawn action requires task"

    agent_loop = context.get("agent_loop")
    if not agent_loop:
        return "Error: agent_loop not available (sub-agent system not enabled)"

    msg_context = context.get("msg_context", {})
    parent_session = msg_context.get("session_id", "unknown")
    person_id = msg_context.get("person_id", "owner")

    # Generate unique run_id
    run_id = str(uuid.uuid4())[:8]
    child_session = f"subagent:{parent_session}:{run_id}"

    # Effective system prompt
    effective_prompt = prompt or (
        f"[Sub-task] You are executing an independent sub-task. "
        f"Return results directly when done.\n\nTask: {task}"
    )

    # Effective tool names
    tool_names = list(tools) if tools else _get_default_tool_names(agent_loop)

    # Determine active profile
    llm_cfg = agent_loop.config.get("llm", {})
    active_profile = llm_cfg.get("active")
    use_separate_agent = llm_profile and llm_profile != active_profile

    # Validate llm_profile if specified
    if use_separate_agent:
        profiles = agent_loop.config.get("llm_profiles", {})
        if llm_profile not in profiles:
            return f"Error: LLM profile '{llm_profile}' not found. Available: {list(profiles.keys())}"

    # Register the run
    run = SubAgentRun(
        run_id=run_id,
        parent_session=parent_session,
        child_session=child_session,
        task=task,
        label=label or f"task-{run_id}",
        status="pending",
        created_at=datetime.now(),
        prompt=effective_prompt,
        tools=tool_names,
        llm_profile=llm_profile,
    )
    await _registry.register(run)

    async def _execute():
        """Run the sub-agent to completion."""
        try:
            await _registry.update_status(run_id, "running")

            # Build agent instance
            # Import here to avoid circular import at module level
            from agent.base import BaseAgent

            if use_separate_agent:
                agent_llm_config = _build_llm_config(agent_loop, llm_profile, max_iterations)
                agent_instance = BaseAgent(
                    agent_id=f"subagent-{run_id}",
                    system_prompt=effective_prompt,
                    llm_config=agent_llm_config,
                )
            else:
                agent_instance = agent_loop.agents.get("default")
                if not agent_instance:
                    raise RuntimeError("No default agent available")

            # Build msg_context for the child session
            child_msg_context = {
                "user_id": msg_context.get("user_id", "system"),
                "person_id": person_id,
                "channel": "subagent",
                "timestamp": datetime.now(),
                "is_group": False,
                "group_id": None,
                "is_owner": True,
                "session_id": child_session,
                "raw": {
                    "parent_session": parent_session,
                    "run_id": run_id,
                    "is_subagent": True,
                },
                "available_channels": agent_loop.dispatcher.list_channels(),
                "contacts": (
                    agent_loop._channel_manager.get_contacts_summary()
                    if agent_loop._channel_manager
                    else {}
                ),
                "attachments": [],
            }

            # Load context (fresh session, no history)
            ctx = await agent_loop.runtime.load_context(
                session_id=child_session,
                query=task,
                person_id=person_id,
                history_limit=0,
            )

            # Tool schemas
            tools_schemas = agent_loop.runtime.get_tool_schemas(tool_names)

            # Tool context
            tool_context = _build_tool_context(agent_loop, person_id, child_session, child_msg_context)

            # Save the user message to the child session
            agent_loop.runtime.save_message(child_session, "user", task)

            # Run the agent
            run_kwargs = dict(
                user_text=task,
                context=ctx,
                tools=tools_schemas,
                tool_context=tool_context,
                msg_context=child_msg_context,
            )
            if use_separate_agent:
                # Separate BaseAgent already has the system prompt baked in
                response = await asyncio.wait_for(
                    agent_instance.run(**run_kwargs),
                    timeout=timeout_seconds,
                )
            else:
                # Reuse default agent, override system prompt
                response = await asyncio.wait_for(
                    agent_instance.run(**run_kwargs, system_prompt_override=effective_prompt),
                    timeout=timeout_seconds,
                )

            # Save the assistant response
            agent_loop.runtime.save_message(child_session, "assistant", response or "")

            await _registry.update_status(run_id, "completed", result=response or "")
            return response or ""

        except asyncio.TimeoutError:
            error_msg = f"Timed out after {timeout_seconds}s"
            await _registry.update_status(run_id, "timeout", error=error_msg)
            logger.warning(f"SubAgent {run_id} timed out: {task[:80]}...")
            return error_msg

        except asyncio.CancelledError:
            await _registry.update_status(run_id, "cancelled", error="Cancelled")
            logger.info(f"SubAgent {run_id} cancelled")
            return "Cancelled"

        except Exception as e:
            error_msg = str(e)
            await _registry.update_status(run_id, "failed", error=error_msg)
            logger.error(f"SubAgent {run_id} failed: {e}", exc_info=True)
            return f"Execution failed: {error_msg}"

    if background:
        task_obj = asyncio.create_task(_execute(), name=f"subagent-{run_id}")
        run._task = task_obj
        logger.info(f"SubAgent spawned (background): run_id={run_id}, task={task[:80]}...")
        return (
            f"Sub-agent started in background.\n"
            f"  run_id: {run_id}\n"
            f"  label:  {run.label}\n"
            f"Use agent(action=\"query\", run_id=\"{run_id}\") to check progress, "
            f"agent(action=\"stop\", run_id=\"{run_id}\") to cancel."
        )
    else:
        result = await _execute()
        return f"[Sub-task completed] run_id={run_id}\n\n{result}"


async def _agent_list(context=None) -> str:
    if not context:
        return "Error: missing execution context"

    msg_context = context.get("msg_context", {})
    parent_session = msg_context.get("session_id", "unknown")

    runs = _registry.list_by_parent(parent_session)
    if not runs:
        return "No sub-agent tasks in this session."

    runs.sort(key=lambda r: r.created_at, reverse=True)

    status_icons = {
        "pending": "⏳",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "timeout": "⏰",
        "cancelled": "🛑",
    }

    lines = ["Sub-agent tasks:", ""]
    for run in runs:
        icon = status_icons.get(run.status, "❓")
        created = run.created_at.strftime("%H:%M:%S")

        line = f"{icon} [{run.run_id}] {run.label} | status: {run.status} | created: {created}"
        if run.completed_at:
            duration = (run.completed_at - run.created_at).total_seconds()
            line += f" | duration: {duration:.1f}s"
        if run.llm_profile:
            line += f" | profile: {run.llm_profile}"
        lines.append(line)

        task_summary = run.task[:80] + "..." if len(run.task) > 80 else run.task
        lines.append(f"   task: {task_summary}")

        if run.status == "completed" and run.result:
            result_summary = run.result[:100] + "..." if len(run.result) > 100 else run.result
            lines.append(f"   result: {result_summary}")
        elif run.error:
            lines.append(f"   error: {run.error}")

        lines.append("")

    return "\n".join(lines)


async def _agent_query(run_id: str, context=None) -> str:
    if not context:
        return "Error: missing execution context"

    run = _registry.get(run_id)
    if not run:
        return f"Error: sub-agent run_id={run_id} not found"

    lines = [
        f"Sub-agent [{run_id}]",
        f"  label:    {run.label}",
        f"  status:   {run.status}",
        f"  created:  {run.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    if run.completed_at:
        lines.append(f"  finished: {run.completed_at.strftime('%Y-%m-%d %H:%M:%S')}")
        duration = (run.completed_at - run.created_at).total_seconds()
        lines.append(f"  duration: {duration:.1f}s")

    if run.llm_profile:
        lines.append(f"  profile:  {run.llm_profile}")

    lines.append(f"  tools:    {', '.join(run.tools) if run.tools else '(none)'}")

    task_display = run.task[:200] + "..." if len(run.task) > 200 else run.task
    lines.append(f"  task:     {task_display}")

    if run.status == "completed" and run.result is not None:
        lines.append(f"\n--- Result ---\n{run.result}")
    elif run.error:
        lines.append(f"\n--- Error ---\n{run.error}")
    elif run.status == "running":
        lines.append("\nThe sub-agent is still running. Use agent(action=\"query\") again later to check.")

    return "\n".join(lines)


async def _agent_send(run_id: str, message: str, context=None) -> str:
    if not context:
        return "Error: missing execution context"

    run = _registry.get(run_id)
    if not run:
        return f"Error: sub-agent run_id={run_id} not found"

    # The ReAct loop does not support mid-execution message injection.
    return (
        f"Sub-agent {run_id} runs autonomously and does not accept mid-execution messages. "
        f"Its current status is: {run.status}.\n"
        f"Use agent(action=\"query\", run_id=\"{run_id}\") to check its progress or result."
    )


async def _agent_stop(run_id: str, context=None) -> str:
    if not context:
        return "Error: missing execution context"

    error = await _registry.stop(run_id)
    if error:
        return f"Error: {error}"

    logger.info(f"SubAgent {run_id} stopped by user")
    return f"Sub-agent {run_id} has been cancelled."


async def _agent_history(run_id: str, limit: int = 10, context=None) -> str:
    if not context:
        return "Error: missing execution context"

    memory = context.get("memory")
    if not memory:
        return "Error: memory not available"

    run = _registry.get(run_id)
    if not run:
        return f"Error: sub-agent run_id={run_id} not found"

    history = memory.get_history(run.child_session, limit=limit)
    if not history:
        return f"Sub-agent {run_id} has no conversation history yet."

    lines = [
        f"Sub-agent [{run_id}] conversation history:",
        f"  status: {run.status} | label: {run.label}",
        "-" * 40,
    ]

    for msg in history:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"]
        if len(content) > 500:
            content = content[:500] + "...(truncated)"
        lines.append(f"\n[{role}]:")
        lines.append(content)

    return "\n".join(lines)


# ===== Public helper =====

def get_subagent_registry() -> SubAgentRegistry:
    """Return the global sub-agent registry (for external use)."""
    return _registry
