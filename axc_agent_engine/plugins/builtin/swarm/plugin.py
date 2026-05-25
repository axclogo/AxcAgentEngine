"""Swarm 插件 — 通过 AgentMessageDispatcher + MessageBus 并行 fan-out。"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, TYPE_CHECKING

from axc_agent_engine.core.constants import MAX_CALL_DEPTH
from axc_agent_engine.core.errors import ErrorCategory, ErrorEnvelope
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.common import bounded_int, externalize_text

if TYPE_CHECKING:
	from axc_agent_engine.plugins import PluginContext

logger = logging.getLogger(__name__)


class SwarmDispatcher:
	def __init__(self, plugin: "SwarmPlugin") -> None:
		self.plugin = plugin

	async def dispatch(self, args: dict, context: dict):
		return await self.plugin._dispatch_swarm(args, context)


class SwarmPlugin(BasePlugin):
	name = "swarm"
	display_name = "并行调度"
	priority = 40
	version = "3.1.0"

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._enabled = config.get("enabled", True)
		self._max_concurrent = bounded_int(config.get("max_concurrent", 5), 1, 100)
		self._max_depth = bounded_int(config.get("max_depth", MAX_CALL_DEPTH), 1, 20)
		self._timeout = _bounded_timeout(config.get("timeout", 60.0), 60.0)
		self._task_timeout = _bounded_timeout(config.get("task_timeout", self._timeout), self._timeout)
		self._allow_self_call = bool(config.get("allow_self_call", False))
		self._allowed_agents = {str(name) for name in config.get("allowed_agents", [])}
		self._denied_agents = {str(name) for name in config.get("denied_agents", [])}
		self._max_result_bytes = bounded_int(config.get("max_result_bytes", 256_000), 1, 10 * 1024 * 1024)
		self._failure_policy = str(config.get("failure_policy", "best_effort"))
		self._plugin_ctx = plugin_ctx
		self._dispatcher = SwarmDispatcher(self)

	def get_tools(self) -> list[ToolDefinition]:
		if not self._enabled:
			return []
		return [ToolDefinition(
			name="swarm_dispatch",
			description="并行调度多个 Agent 执行独立任务并返回标准化结果",
			parameters={"type": "object", "properties": {
				"goal": {"type": "string", "description": "总体目标"},
				"tasks": {"type": "array", "description": "任务列表", "items": {"type": "object", "properties": {
					"task_id": {"type": "string", "description": "可选任务 ID"},
					"agent_name": {"type": "string"},
					"description": {"type": "string"},
					"timeout": {"type": "number", "description": "单任务超时时间（秒）"},
					"priority": {"type": "integer", "description": "优先级，数字越小越先调度", "default": 100},
				}, "required": ["agent_name", "description"]}},
				"timeout": {"type": "number", "description": "本次 swarm 总超时时间（秒）"},
				"failure_policy": {"type": "string", "enum": ["best_effort", "fail_fast"], "default": self._failure_policy},
			}, "required": ["goal", "tasks"]},
			is_read_only=False,
			capability="agent_call",
			risk_level="moderate",
			execute=self._tool_swarm_dispatch,
		)]

	async def _tool_swarm_dispatch(self, args: dict, context: dict):
		return await self._dispatcher.dispatch(args, context)

	async def _dispatch_swarm(self, args: dict, context: dict):
		from axc_agent_engine.core.dispatcher import AgentEnvelope
		from axc_agent_engine.tools.tool_output import ToolOutput

		started = time.time()
		goal = str(args.get("goal", ""))
		tasks = args.get("tasks", [])
		if not isinstance(tasks, list) or not tasks:
			return ToolOutput.error("tasks 不能为空")
		dispatcher = self._plugin_ctx.dispatcher
		if not dispatcher:
			return ToolOutput.error("MessageBus not configured, cannot dispatch swarm")
		exec_ctx: Any = context.get("exec_ctx")
		current_depth = int(getattr(getattr(exec_ctx, "runtime", None), "agent_call_depth", 0) or 0) if exec_ctx else 0
		if current_depth >= self._max_depth:
			return ToolOutput.error(f"调用深度超过限制({self._max_depth}层)")
		caller = _caller_name(context, exec_ctx)
		agent_map = {a.name: a for a in self._plugin_ctx.list_agents()}
		normalized, validation_error = self._normalize_tasks(tasks, agent_map, caller)
		if validation_error:
			return ToolOutput.error(validation_error)
		normalized.sort(key=lambda task: (task["priority"], task["index"]))
		total_timeout = _bounded_timeout(args.get("timeout", self._timeout), self._timeout)
		failure_policy = str(args.get("failure_policy", self._failure_policy))
		if failure_policy not in {"best_effort", "fail_fast"}:
			return ToolOutput.error("failure_policy must be best_effort or fail_fast")
		swarm_id = uuid.uuid4().hex[:12]
		semaphore = asyncio.Semaphore(self._max_concurrent)
		metadata_base = _swarm_metadata(context, exec_ctx, current_depth + 1, swarm_id, goal)
		if exec_ctx:
			exec_ctx.runtime.agent_call_depth = current_depth + 1
		try:
			async def _run_task(task: dict) -> dict:
				async with semaphore:
					task_start = time.time()
					task_metadata = {**metadata_base, "swarm_task_id": task["task_id"], "swarm_task_index": task["index"]}
					envelope = AgentEnvelope(
						sender=caller or "swarm",
						recipient=task["agent_name"],
						content=task["description"],
						conversation_id=str(context.get("session_id", "")),
						trace_id=str(task_metadata.get("trace_id", "")),
						metadata=task_metadata,
					)
					try:
						reply = await dispatcher.request(envelope, timeout=task["timeout"])
						duration_ms = int((time.time() - task_start) * 1000)
						if reply.type == "error":
							return _task_result(task, "error", duration_ms=duration_ms, error=reply.content)
						result_payload = str(reply.content)
						content, artifact = await _externalize_result(
							result_payload,
							context.get("result_store"),
							self._max_result_bytes,
							{"kind": "swarm_result", "agent_name": task["agent_name"], "task_id": task["task_id"], "swarm_id": swarm_id},
						)
						return _task_result(task, "success", duration_ms=duration_ms, result=content, artifact=artifact)
					except Exception as e:
						return _task_result(task, "error", duration_ms=int((time.time() - task_start) * 1000), error=str(e))

			results = await asyncio.wait_for(_gather_swarm(normalized, _run_task, failure_policy), timeout=total_timeout)
		except asyncio.TimeoutError:
			results = [_task_result(task, "cancelled", error=f"swarm timeout after {total_timeout}s") for task in normalized]
		finally:
			if exec_ctx:
				exec_ctx.runtime.agent_call_depth = current_depth
		results.sort(key=lambda item: item["index"])
		success_count = sum(1 for item in results if item["status"] == "success")
		error_count = sum(1 for item in results if item["status"] == "error")
		cancelled_count = sum(1 for item in results if item["status"] == "cancelled")
		payload = {
			"swarm_id": swarm_id,
			"goal": goal,
			"total": len(normalized),
			"success": success_count,
			"error": error_count,
			"cancelled": cancelled_count,
			"duration_ms": int((time.time() - started) * 1000),
			"failure_policy": failure_policy,
			"results": results,
		}
		await self._record(exec_ctx, payload)
		artifacts = [item.pop("_artifact_ref") for item in results if item.get("_artifact_ref")]
		return ToolOutput(
			content=payload,
			content_type="json",
			summary=f"Swarm：{success_count}/{len(normalized)} 个任务成功",
			artifacts=artifacts,
			metadata={"swarm_id": swarm_id, "capability": "agent_call", "risk_level": "moderate"},
		)

	def _normalize_tasks(self, tasks: list, agent_map: dict, caller: str) -> tuple[list[dict], str]:
		normalized: list[dict] = []
		for index, raw in enumerate(tasks):
			if not isinstance(raw, dict):
				return [], "task must be object"
			agent_name = str(raw.get("agent_name", ""))
			description = str(raw.get("description", ""))
			if not agent_name or not description:
				return [], "agent_name 和 description 不能为空"
			if agent_name not in agent_map:
				return [], f"Agent '{agent_name}' 不存在"
			if not self._is_agent_allowed(agent_name, caller):
				return [], f"Agent '{agent_name}' is not allowed"
			normalized.append({
				"task_id": str(raw.get("task_id") or f"task-{index + 1}"),
			"index": index,
			"agent_name": agent_name,
			"description": description,
			"timeout": _bounded_timeout(raw.get("timeout", self._task_timeout), self._task_timeout),
			"priority": bounded_int(raw.get("priority", 100), 0, 100000),
		})
		return normalized, ""

	def _is_agent_allowed(self, agent_name: str, caller: str = "") -> bool:
		if not agent_name:
			return False
		if not self._allow_self_call and caller and agent_name == caller:
			return False
		if agent_name in self._denied_agents:
			return False
		return not self._allowed_agents or agent_name in self._allowed_agents

	async def _record(self, exec_ctx: Any, payload: dict[str, Any]) -> None:
		if not exec_ctx:
			return
		exec_ctx.state.metadata["swarm"] = {
			"swarm_id": payload["swarm_id"],
			"total": payload["total"],
			"success": payload["success"],
			"error": payload["error"],
			"cancelled": payload["cancelled"],
			"duration_ms": payload["duration_ms"],
		}
		audit_sink = getattr(getattr(exec_ctx, "services", None), "audit_sink", None)
		if not audit_sink:
			return
		from axc_agent_engine.observability.audit import AuditEvent
		metadata = exec_ctx.state.metadata
		error = {}
		if payload["error"] or payload["cancelled"]:
			error = ErrorEnvelope(
				code="swarm.partial_failure",
				message="One or more swarm tasks failed",
				category=ErrorCategory.TOOL,
				retryable=True,
				details={"error": payload["error"], "cancelled": payload["cancelled"]},
			).to_dict()
		await audit_sink.record(AuditEvent(
			type="swarm_dispatch_completed",
			actor=str(metadata.get("agent_name") or ""),
			session_id=str(metadata.get("session_id") or ""),
			tool_name="swarm_dispatch",
			capability="agent_call",
			risk_level="moderate",
			allowed=True,
			duration_ms=payload["duration_ms"],
			error=error,
			metadata={k: v for k, v in payload.items() if k != "results"},
		))


async def _gather_swarm(tasks: list[dict], runner, failure_policy: str) -> list[dict]:
	if failure_policy == "best_effort":
		return await asyncio.gather(*[runner(task) for task in tasks])
	pending = {asyncio.create_task(runner(task)): task for task in tasks}
	results: list[dict] = []
	try:
		while pending:
			done, _ = await asyncio.wait(pending.keys(), return_when=asyncio.FIRST_COMPLETED)
			for task_future in done:
				pending.pop(task_future)
				result = await task_future
				results.append(result)
				if result["status"] != "success":
					for pending_future, pending_task in pending.items():
						pending_future.cancel()
						results.append(_task_result(pending_task, "cancelled", error="cancelled by fail_fast"))
					await asyncio.gather(*pending.keys(), return_exceptions=True)
					return results
		return results
	finally:
		for future in pending:
			future.cancel()


def _task_result(task: dict, status: str, duration_ms: int = 0, result: Any = "", error: str = "", artifact: Any = None) -> dict:
	payload = {
		"task_id": task["task_id"],
		"index": task["index"],
		"agent_name": task["agent_name"],
		"status": status,
		"duration_ms": duration_ms,
		"result": result,
		"error": error,
	}
	if artifact:
		payload["artifact_id"] = artifact.id
		payload["artifact"] = artifact.to_dict()
		payload["_artifact_ref"] = artifact
	return payload


def _caller_name(context: dict, exec_ctx: Any) -> str:
	if context.get("agent_name"):
		return str(context.get("agent_name"))
	agent_info = getattr(getattr(exec_ctx, "runtime", None), "agent_info", None)
	if agent_info and agent_info.name:
		return agent_info.name
	return str(getattr(getattr(exec_ctx, "state", None), "metadata", {}).get("agent_name", "") if exec_ctx else "")


def _swarm_metadata(context: dict, exec_ctx: Any, depth: int, swarm_id: str, goal: str) -> dict[str, Any]:
	metadata: dict[str, Any] = {}
	state_metadata = getattr(getattr(exec_ctx, "state", None), "metadata", None)
	if isinstance(state_metadata, dict):
		metadata.update(state_metadata)
	metadata.update({
		"agent_call_depth": depth,
		"caller_agent": context.get("agent_name", metadata.get("agent_name", "")),
		"caller_session_id": context.get("session_id", metadata.get("session_id", "")),
		"swarm_id": swarm_id,
		"swarm_goal": goal,
	})
	if not metadata.get("trace_id"):
		metadata["trace_id"] = str(metadata.get("run_id") or swarm_id)
	return metadata


async def _externalize_result(content: str, result_store: Any, max_result_bytes: int, metadata: dict[str, Any]) -> tuple[Any, Any]:
	return await externalize_text(content, result_store, max_result_bytes, metadata, logger, "swarm")


def _bounded_timeout(value: Any, default: float) -> float:
	try:
		timeout = float(value)
	except (TypeError, ValueError):
		timeout = default
	return max(1.0, min(timeout, 3600.0))
