"""Runtime objects for tool orchestration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.tools.context import ToolContext
from axc_agent_engine.tools.registry import ToolRegistry


@dataclass
class ToolCallRuntime:
	"""单次工具调用的运行时上下文。"""
	name: str
	arguments: dict
	tool_call_id: str
	actor: str
	session_id: str
	tool_def: Any = None


def step_timeout(ctx: ExecutionContext) -> float:
	timeout = getattr(ctx.config, "step_timeout", 0)
	return float(timeout) if timeout and timeout > 0 else 0.0


def tool_runtime(tc: dict, ctx: ExecutionContext, registry: ToolRegistry) -> ToolCallRuntime:
	name = tc.get("name", "")
	return ToolCallRuntime(
		name=name,
		arguments=tc.get("arguments", {}),
		tool_call_id=tc.get("id", ""),
		actor=ctx.state.metadata.get("agent_name", ""),
		session_id=ctx.state.metadata.get("session_id", ""),
		tool_def=registry.get(name),
	)


def push_current_tool_runtime(ctx: ExecutionContext, runtime: ToolCallRuntime) -> int:
	"""Store per-task tool metadata so observability plugins can correlate hooks safely."""
	task = asyncio.current_task()
	key = id(task) if task else id(runtime)
	contexts = ctx.runtime.plugin_states.setdefault("_tool_runtime_contexts", {})
	contexts[key] = {
		"tool_name": runtime.name,
		"tool_call_id": runtime.tool_call_id,
		"actor": runtime.actor,
		"session_id": runtime.session_id,
		"capability": getattr(runtime.tool_def, "capability", "") if runtime.tool_def else "",
		"risk_level": getattr(runtime.tool_def, "risk_level", "") if runtime.tool_def else "",
		"is_read_only": bool(getattr(runtime.tool_def, "is_read_only", False)) if runtime.tool_def else False,
	}
	return key


def pop_current_tool_runtime(ctx: ExecutionContext, key: int) -> None:
	contexts = ctx.runtime.plugin_states.get("_tool_runtime_contexts")
	if isinstance(contexts, dict):
		contexts.pop(key, None)


def tool_context(ctx: ExecutionContext, runtime: ToolCallRuntime) -> ToolContext:
	return ToolContext(
		workspace=ctx.config.workspace if hasattr(ctx.config, "workspace") else "",
		exec_ctx=ctx,
		session_id=ctx.state.metadata.get("session_id", ""),
		agent_name=ctx.state.metadata.get("agent_name", ""),
		tool_name=runtime.name,
		tool_call_id=runtime.tool_call_id,
		request_queue=ctx.runtime.approval_queue,
		response_queue=ctx.runtime.response_queue,
	)
