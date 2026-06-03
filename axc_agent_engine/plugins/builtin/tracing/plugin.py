"""Tracing 插件 — 引擎内标准链路追踪层。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any, Callable

from axc_agent_engine.core.errors import ErrorCategory, ErrorEnvelope
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import TRACING_CONFIG_SCHEMA

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext
	from axc_agent_engine.tools.tool_output import ToolOutput

logger = logging.getLogger(__name__)

_DEFAULT_REDACT_KEYS = {
	"api_key",
	"apikey",
	"authorization",
	"cookie",
	"password",
	"secret",
	"token",
}


class RedactionService:
	def __init__(self, redact_keys: set[str], max_argument_length: int, stats: dict[str, int]) -> None:
		self.redact_keys = redact_keys
		self.max_argument_length = max_argument_length
		self.stats = stats

	def redact(self, value: Any) -> Any:
		return _redact(value, self.redact_keys, self.max_argument_length, self.stats)


class TraceSampler:
	def __init__(self, sample_rate: float, sample_errors: bool, slow_span_ms: int) -> None:
		self.sample_rate = sample_rate
		self.sample_errors = sample_errors
		self.slow_span_ms = slow_span_ms

	def sampled(self, trace_id: str) -> bool:
		return _sampled(trace_id, self.sample_rate)

	def should_emit(self, span: dict[str, Any], force_sample: bool) -> bool:
		if force_sample and self.sample_errors:
			return True
		if self.slow_span_ms and int(span.get("duration_ms", 0)) >= self.slow_span_ms:
			return True
		return bool(span.get("sampled", True))


class SpanFactory:
	def new_span(
		self,
		exec_ctx: "ExecutionContext",
		state: dict[str, Any],
		span_type: str,
		name: str,
		parent_span_id: str | None = "",
		extra: dict[str, Any] | None = None,
	) -> dict[str, Any]:
		root = state.get("root_span")
		parent_id = parent_span_id if parent_span_id != "" else (root.get("span_id") if root else None)
		metadata = exec_ctx.state.metadata
		model_info = getattr(exec_ctx.runtime, "model_info", None)
		agent_info = getattr(exec_ctx.runtime, "agent_info", None)
		span = {
			"trace_id": state.get("trace_id") or _trace_id(exec_ctx),
			"traceparent": state.get("traceparent", ""),
			"span_id": uuid.uuid4().hex[:16],
			"parent_span_id": parent_id,
			"type": span_type,
			"name": name,
			"start_time": time.time(),
			"agent_name": str(metadata.get("agent_name") or getattr(agent_info, "name", "") or ""),
			"session_id": str(metadata.get("session_id") or getattr(agent_info, "session_id", "") or ""),
			"run_id": str(metadata.get("run_id", "")),
			"workspace": str(getattr(exec_ctx.config, "workspace", "") or getattr(agent_info, "workspace", "") or ""),
			"routing_mode": str(getattr(agent_info, "routing_mode", "") or metadata.get("routing_mode", "")),
			"model": getattr(model_info, "active", "") or getattr(model_info, "default", "") if model_info else "",
			"sampled": bool(state.get("sampled", True)),
			"metadata": _span_metadata(metadata),
		}
		if extra:
			span.update(extra)
		if root and span is not root:
			children = root.setdefault("children", [])
			children.append(span["span_id"])
		return span


class SpanEmitter:
	def __init__(self, plugin: "TracingPlugin") -> None:
		self.plugin = plugin

	def emit(self, span: dict[str, Any], force_sample: bool = False) -> None:
		plugin = self.plugin
		if not plugin._should_emit(span, force_sample):
			plugin._stats["dropped"] += 1
			return
		plugin._stats["emitted"] += 1
		plugin._recent_spans.append(dict(span))
		if plugin._span_store:
			plugin._schedule(plugin._save_span(span))
		if plugin._exporter:
			result = plugin._exporter(dict(span))
			if hasattr(result, "__await__"):
				plugin._schedule(result)
		if plugin._callback:
			plugin._callback(dict(span))
			return
		if plugin._output == "log":
			_log_span(span)


class TraceToolHandlers:
	def __init__(self, plugin: "TracingPlugin") -> None:
		self.plugin = plugin

	async def status(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		plugin = self.plugin
		return ToolOutput.json_output({
			"enabled": plugin._enabled,
			"output": plugin._output,
			"has_span_store": bool(plugin._span_store),
			"has_callback": bool(plugin._callback),
			"has_exporter": bool(plugin._exporter),
			"sample_rate": plugin._sample_rate,
			"sample_errors": plugin._sample_errors,
			"slow_span_ms": plugin._slow_span_ms,
			"recent_limit": plugin._recent_limit,
			"queue_limit": plugin._queue_limit,
			"pending": len(plugin._pending_tasks),
			"stats": dict(plugin._stats),
		}, summary=f"tracing emitted={plugin._stats['emitted']} stored={plugin._stats['stored']}")


class TracingPlugin(BasePlugin):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
标准化 trace/span 采集，支持 SpanStore、callback、log 和查询工具。"""
	name = "tracing"
	display_name = "链路追踪"
	priority = 1
	version = "3.0.0"
	config_schema = TRACING_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		super().initialize(config, plugin_ctx)
		self._enabled = bool(config.get("enabled", True))
		self._output = str(config.get("output", "log"))
		self._include_args = bool(config.get("include_arguments", False))
		self._include_result = bool(config.get("include_result", False))
		self._max_argument_length = _strict_int(config.get("max_argument_length", 2000), 1, 200_000, "tracing.max_argument_length")
		self._max_result_len = _strict_int(config.get("max_result_length", 200), 1, 200_000, "tracing.max_result_length")
		self._max_error_length = _strict_int(config.get("max_error_length", 2000), 1, 200_000, "tracing.max_error_length")
		self._sample_rate = _strict_float(config.get("sample_rate", 1.0), 0.0, 1.0, "tracing.sample_rate")
		self._sample_errors = bool(config.get("sample_errors", True))
		self._slow_span_ms = _strict_int(config.get("slow_span_ms", 0), 0, 3_600_000, "tracing.slow_span_ms")
		self._recent_limit = _strict_int(config.get("recent_limit", 200), 1, 10_000, "tracing.recent_limit")
		self._queue_limit = _strict_int(config.get("queue_limit", 1000), 1, 100_000, "tracing.queue_limit")
		self._redact_keys = {str(k).lower() for k in config.get("redact_keys", [])} | _DEFAULT_REDACT_KEYS
		self._audit_mode = bool(config.get("audit_mode", False))
		self._exporter: Callable[[dict], Any] | None = plugin_ctx.resources.get("tracing.exporter")
		self._callback: Callable[[dict], None] | None = None
		self._span_store = getattr(plugin_ctx, "span_store", None)
		self._kv_store = getattr(plugin_ctx, "kv_store", None)
		self._recent_spans: deque[dict[str, Any]] = deque(maxlen=self._recent_limit)
		self._pending_tasks: set[asyncio.Task] = set()
		self._task_errors: list[BaseException] = []
		self._stats: dict[str, int] = {"emitted": 0, "stored": 0, "dropped": 0, "failed": 0, "redacted": 0}
		self._redaction = RedactionService(self._redact_keys, self._max_argument_length, self._stats)
		self._sampler = TraceSampler(self._sample_rate, self._sample_errors, self._slow_span_ms)
		self._span_factory = SpanFactory()
		self._span_emitter = SpanEmitter(self)
		self._tool_handlers = TraceToolHandlers(self)

	def set_callback(self, callback: Callable[[dict], None]) -> None:
		self._callback = callback

	def get_tools(self) -> list[ToolDefinition]:
		if not self._enabled:
			return []
		return [
			ToolDefinition(
				name="trace_status",
				description="查看 tracing 插件状态、配置和写入统计",
				parameters={"type": "object", "properties": {}},
				is_read_only=True,
				capability="trace_read",
				risk_level="safe",
				execute=self._tool_trace_status,
			),
			ToolDefinition(
				name="get_trace",
				description="按 trace_id 查询 span 列表",
				parameters={
					"type": "object",
					"properties": {
						"trace_id": {"type": "string", "description": "追踪 ID"},
					},
					"required": ["trace_id"],
				},
				is_read_only=True,
				capability="trace_read",
				risk_level="safe",
				execute=self._tool_get_trace,
			),
			ToolDefinition(
				name="list_traces",
				description="列出最近采集的 trace 摘要",
				parameters={
					"type": "object",
					"properties": {
						"session_id": {"type": "string", "description": "可选会话 ID", "default": ""},
						"limit": {"type": "integer", "description": "返回数量", "default": 20},
					},
				},
				is_read_only=True,
				capability="trace_read",
				risk_level="safe",
				execute=self._tool_list_traces,
			),
		]

	def _state(self, exec_ctx: "ExecutionContext") -> dict[str, Any]:
		return exec_ctx.get_plugin_state(self.name, lambda: {
			"trace_id": "",
			"traceparent": "",
			"sampled": True,
			"active_spans": {},
			"root_span": None,
			"current_llm_span": None,
		})

	async def on_execution_start(self, exec_ctx: "ExecutionContext") -> None:
		if not self._enabled:
			return
		state = self._state(exec_ctx)
		trace_id = _trace_id(exec_ctx)
		sampled = self._sampler.sampled(trace_id)
		state.update({
			"trace_id": trace_id,
			"traceparent": _traceparent(trace_id),
			"sampled": sampled,
			"active_spans": {},
			"current_llm_span": None,
		})
		root_span = self._new_span(exec_ctx, state, "execution", "execution", parent_span_id=None)
		state["root_span"] = root_span
		self._sync_metadata(exec_ctx, state)

	async def on_execution_end(self, exec_ctx: "ExecutionContext", result: str, error: str) -> None:
		if not self._enabled:
			return
		state = self._state(exec_ctx)
		root_span = state.get("root_span")
		if not root_span:
			return
		error_payload = _error_payload(error, self._max_error_length) if error else {}
		self._finish_span(
			exec_ctx,
			state,
			root_span,
			success=not error,
			extra={
				"input_tokens": exec_ctx.state.total_input_tokens,
				"output_tokens": exec_ctx.state.total_output_tokens,
				"round": exec_ctx.state.current_round,
			},
			error=error_payload,
		)
		self._sync_metadata(exec_ctx, state)

	def pre_llm_call(self, exec_ctx: "ExecutionContext", messages: list[dict],
					 tools: list[dict] | None) -> tuple[list[dict], list[dict] | None]:
		if not self._enabled:
			return messages, tools
		state = self._state(exec_ctx)
		span = self._new_span(
			exec_ctx,
			state,
			"llm_call",
			f"llm_call_round_{exec_ctx.state.current_round}",
			extra={
				"round": exec_ctx.state.current_round,
				"message_count": len(messages),
				"tool_schema_count": len(tools or []),
			},
		)
		state["current_llm_span"] = span
		return messages, tools

	async def post_llm_call(self, exec_ctx: "ExecutionContext", messages: list[dict],
							response: dict, duration_ms: int) -> None:
		if not self._enabled:
			return
		state = self._state(exec_ctx)
		span = state.get("current_llm_span")
		if not span:
			return
		usage = response.get("usage", {}) if isinstance(response, dict) else {}
		total_usage = response.get("total_usage", {}) if isinstance(response, dict) else {}
		self._finish_span(
			exec_ctx,
			state,
			span,
			duration_ms=duration_ms,
			extra={
				"input_tokens": usage.get("input_tokens", 0),
				"output_tokens": usage.get("output_tokens", 0),
				"total_input_tokens": total_usage.get("input_tokens", exec_ctx.state.total_input_tokens),
				"total_output_tokens": total_usage.get("output_tokens", exec_ctx.state.total_output_tokens),
			},
		)
		state["current_llm_span"] = None

	async def on_error(self, exec_ctx: "ExecutionContext", error: Exception) -> None:
		if not self._enabled:
			return
		state = self._state(exec_ctx)
		span = self._new_span(exec_ctx, state, "error", error.__class__.__name__)
		self._finish_span(
			exec_ctx,
			state,
			span,
			success=False,
			error=_error_payload(error, self._max_error_length),
			force_sample=True,
		)

	async def pre_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
							arguments: dict) -> tuple[bool, dict]:
		if not self._enabled:
			return True, arguments
		state = self._state(exec_ctx)
		tool_runtime = _current_tool_runtime(exec_ctx)
		tool_call_id = str(tool_runtime.get("tool_call_id") or uuid.uuid4().hex[:12])
		extra = {
			"round": exec_ctx.state.current_round,
			"tool_call_id": tool_call_id,
			"capability": tool_runtime.get("capability", ""),
			"risk_level": tool_runtime.get("risk_level", ""),
			"is_read_only": tool_runtime.get("is_read_only", False),
		}
		if self._include_args:
			extra["arguments"] = self._redaction.redact(arguments)
			extra["argument_keys"] = sorted(arguments.keys())
		else:
			extra["argument_keys"] = sorted(arguments.keys())
		span = self._new_span(exec_ctx, state, "tool_call", tool_name, extra=extra)
		state.setdefault("active_spans", {})[tool_call_id] = span
		return True, arguments

	async def post_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
							 arguments: dict, result: "ToolOutput", duration_ms: int) -> "ToolOutput":
		if not self._enabled:
			return result
		state = self._state(exec_ctx)
		tool_runtime = _current_tool_runtime(exec_ctx)
		tool_call_id = str(tool_runtime.get("tool_call_id") or "")
		span = state.setdefault("active_spans", {}).pop(tool_call_id, None) if tool_call_id else None
		result_str = result.context_view() if result else ""
		success = not result.is_error if result else True
		error = (
			_error_payload(result_str, self._max_error_length, code="tool.output_error")
			if result and result.is_error else {}
		)
		extra = {
			"tool_call_id": tool_call_id,
			"content_type": getattr(result, "content_type", ""),
			"artifact_count": len(getattr(result, "artifacts", []) or []),
		}
		if self._include_result:
			extra["result"] = _truncate(result_str, self._max_result_len)
		if span:
			self._finish_span(
				exec_ctx,
				state,
				span,
				duration_ms=duration_ms,
				success=success,
				extra=extra,
				error=error,
				force_sample=not success,
			)
		if self._audit_mode and self._kv_store:
			audit_entry = {
				"timestamp": time.time(),
				"tool_name": tool_name,
				"tool_call_id": tool_call_id,
				"arguments": self._redaction.redact(arguments),
				"result_preview": _truncate(result_str, 500),
				"duration_ms": duration_ms,
				"session_id": exec_ctx.state.metadata.get("session_id", ""),
				"trace_id": state["trace_id"],
			}
			self._schedule(self._kv_store.set(f"audit:{uuid.uuid4().hex}", audit_entry))
		return result

	async def on_tool_call_failed(self, exec_ctx: "ExecutionContext", tool_name: str,
								  arguments: dict, error: dict, duration_ms: int) -> None:
		if not self._enabled:
			return
		state = self._state(exec_ctx)
		tool_runtime = _current_tool_runtime(exec_ctx)
		tool_call_id = str(tool_runtime.get("tool_call_id") or "")
		span = state.setdefault("active_spans", {}).pop(tool_call_id, None) if tool_call_id else None
		if span:
			self._finish_span(
				exec_ctx,
				state,
				span,
				duration_ms=duration_ms,
				success=False,
				extra={"tool_call_id": tool_call_id},
				error=error or _error_payload("tool call failed", self._max_error_length, code="tool.execution_failed"),
				force_sample=True,
			)

	async def on_round_end(self, exec_ctx: "ExecutionContext", user_message: str,
						   assistant_message: str, tool_calls: list[dict]) -> None:
		if not self._enabled:
			return
		state = self._state(exec_ctx)
		span = self._new_span(
			exec_ctx,
			state,
			"round_end",
			f"round_{exec_ctx.state.current_round}",
			extra={
				"round": exec_ctx.state.current_round,
				"input_tokens": exec_ctx.state.total_input_tokens,
				"output_tokens": exec_ctx.state.total_output_tokens,
				"tool_count": len(tool_calls),
			},
		)
		self._finish_span(exec_ctx, state, span, duration_ms=0)

	async def close(self) -> None:
		await self._flush_pending()

	async def _tool_trace_status(self, args: dict, context: dict):
		return await self._tool_handlers.status(args, context)

	async def _tool_get_trace(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		trace_id = str(args.get("trace_id", ""))
		if not trace_id:
			return ToolOutput.error("trace_id 不能为空")
		spans = []
		if self._span_store:
			spans = await self._span_store.query_by_trace(trace_id)
		if not spans:
			spans = [span for span in self._recent_spans if span.get("trace_id") == trace_id]
		spans = sorted(spans, key=lambda item: (item.get("start_time", 0), item.get("span_id", "")))
		return ToolOutput.json_output({
			"trace_id": trace_id,
			"spans": spans,
			"count": len(spans),
			"summary": _trace_summary(spans),
		}, summary=f"trace {trace_id}: {len(spans)} spans")

	async def _tool_list_traces(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		limit = _bounded_int(args.get("limit", 20), 1, 200)
		session_id = str(args.get("session_id", ""))
		by_trace: dict[str, dict[str, Any]] = {}
		for span in list(self._recent_spans):
			if session_id and span.get("session_id") != session_id:
				continue
			trace_id = span.get("trace_id", "")
			if not trace_id:
				continue
			item = by_trace.setdefault(trace_id, {
				"trace_id": trace_id,
				"session_id": span.get("session_id", ""),
				"agent_name": span.get("agent_name", ""),
				"span_count": 0,
				"errors": 0,
				"start_time": span.get("start_time", 0),
				"last_time": span.get("end_time", span.get("start_time", 0)),
			})
			item["span_count"] += 1
			item["errors"] += 1 if span.get("error") else 0
			item["start_time"] = min(item["start_time"], span.get("start_time", item["start_time"]))
			item["last_time"] = max(item["last_time"], span.get("end_time", span.get("start_time", item["last_time"])))
		traces = sorted(by_trace.values(), key=lambda item: item["last_time"], reverse=True)[:limit]
		return ToolOutput.json_output({"traces": traces, "count": len(traces)}, summary=f"找到 {len(traces)} 条 trace")

	def _new_span(self, exec_ctx: "ExecutionContext", state: dict[str, Any], span_type: str, name: str,
				  parent_span_id: str | None = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
		return self._span_factory.new_span(exec_ctx, state, span_type, name, parent_span_id, extra)

	def _finish_span(self, exec_ctx: "ExecutionContext", state: dict[str, Any], span: dict[str, Any],
					 duration_ms: int | None = None, success: bool = True,
					 extra: dict[str, Any] | None = None, error: dict[str, Any] | None = None,
					 force_sample: bool = False) -> None:
		end_time = time.time()
		span["end_time"] = end_time
		span["duration_ms"] = int(duration_ms if duration_ms is not None else (end_time - span["start_time"]) * 1000)
		span["success"] = success
		if extra:
			span.update(extra)
		if error:
			span["error"] = error
		self._emit(span, force_sample=force_sample or bool(error))
		self._sync_metadata(exec_ctx, state)

	def _emit(self, span: dict[str, Any], force_sample: bool = False) -> None:
		self._span_emitter.emit(span, force_sample)

	def _should_emit(self, span: dict[str, Any], force_sample: bool) -> bool:
		return self._sampler.should_emit(span, force_sample)

	async def _save_span(self, span: dict[str, Any]) -> None:
		await self._span_store.save_span(dict(span))
		self._stats["stored"] += 1

	def _schedule(self, coro: Any) -> None:
		if len(self._pending_tasks) >= self._queue_limit:
			self._stats["dropped"] += 1
			if hasattr(coro, "close"):
				coro.close()
			raise RuntimeError("tracing async export queue is full")
		try:
			loop = asyncio.get_running_loop()
			task = loop.create_task(coro)
		except RuntimeError:
			self._stats["dropped"] += 1
			if hasattr(coro, "close"):
				coro.close()
			raise RuntimeError("tracing async output requires a running event loop")
		self._pending_tasks.add(task)
		task.add_done_callback(self._on_task_done)

	def _on_task_done(self, task: asyncio.Task) -> None:
		self._pending_tasks.discard(task)
		if task.cancelled():
			return
		exc = task.exception()
		if exc:
			self._stats["failed"] += 1
			self._task_errors.append(exc)

	async def _flush_pending(self) -> None:
		if not self._pending_tasks:
			self._raise_task_errors()
			return
		tasks = list(self._pending_tasks)
		self._pending_tasks.clear()
		await asyncio.gather(*tasks)
		self._raise_task_errors()

	def _raise_task_errors(self) -> None:
		if not self._task_errors:
			return
		error = self._task_errors.pop(0)
		self._task_errors.clear()
		raise RuntimeError("tracing background task failed") from error

	def _sync_metadata(self, exec_ctx: "ExecutionContext", state: dict[str, Any]) -> None:
		exec_ctx.state.metadata["tracing"] = {
			"trace_id": state.get("trace_id", ""),
			"traceparent": state.get("traceparent", ""),
			"sampled": bool(state.get("sampled", True)),
			"emitted": self._stats["emitted"],
			"dropped": self._stats["dropped"],
		}

def _current_tool_runtime(exec_ctx: "ExecutionContext") -> dict[str, Any]:
	task = asyncio.current_task()
	key = id(task) if task else 0
	contexts = exec_ctx.runtime.plugin_states.get("_tool_runtime_contexts", {})
	if isinstance(contexts, dict):
		return dict(contexts.get(key, {}))
	return {}


def _span_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
	"""Copy execution metadata into a JSON-safe span payload.
中文：将执行 metadata 复制为 span 可保存载荷。"""
	return {key: _span_metadata_value(value) for key, value in metadata.items()}


def _span_metadata_value(value: Any) -> Any:
	if isinstance(value, (str, int, float, bool)) or value is None:
		return value
	if isinstance(value, list):
		return [_span_metadata_value(item) for item in value]
	if isinstance(value, tuple):
		return [_span_metadata_value(item) for item in value]
	if isinstance(value, dict):
		return {str(key): _span_metadata_value(item) for key, item in value.items()}
	return str(value)


def _trace_id(exec_ctx: "ExecutionContext") -> str:
	metadata = exec_ctx.state.metadata
	existing = str(metadata.get("trace_id") or metadata.get("run_id") or "")
	if existing:
		return hashlib.sha256(existing.encode("utf-8")).hexdigest()[:32]
	return uuid.uuid4().hex


def _traceparent(trace_id: str) -> str:
	trace_id = (trace_id or uuid.uuid4().hex)[:32].ljust(32, "0")
	return f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"


def _sampled(trace_id: str, sample_rate: float) -> bool:
	if sample_rate >= 1.0:
		return True
	if sample_rate <= 0.0:
		return False
	value = int(hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
	return value <= sample_rate


def _redact(value: Any, redact_keys: set[str], max_length: int, stats: dict[str, int]) -> Any:
	if isinstance(value, dict):
		redacted = {}
		for key, item in value.items():
			if str(key).lower() in redact_keys:
				redacted[key] = "[REDACTED]"
				stats["redacted"] += 1
			else:
				redacted[key] = _redact(item, redact_keys, max_length, stats)
		return redacted
	if isinstance(value, list):
		return [_redact(item, redact_keys, max_length, stats) for item in value[:100]]
	if isinstance(value, str):
		return _truncate(value, max_length)
	return value


def _truncate(value: Any, max_length: int) -> str:
	text = str(value)
	if len(text) <= max_length:
		return text
	omitted = len(text) - max_length
	return f"{text[:max_length]}...[省略 {omitted} 个字符]"


def _error_payload(error: Any, max_length: int, code: str = "trace.error") -> dict[str, Any]:
	if isinstance(error, ErrorEnvelope):
		return error.to_dict()
	if isinstance(error, Exception):
		return ErrorEnvelope(
			code=code,
			message=_truncate(str(error), max_length),
			category=ErrorCategory.INTERNAL,
			retryable=False,
			details={"class": error.__class__.__name__},
		).to_dict()
	return ErrorEnvelope(
		code=code,
		message=_truncate(str(error), max_length),
		category=ErrorCategory.TOOL,
		retryable=False,
	).to_dict()


def _trace_summary(spans: list[dict[str, Any]]) -> dict[str, Any]:
	return {
		"span_count": len(spans),
		"errors": sum(1 for span in spans if span.get("error")),
		"tool_calls": sum(1 for span in spans if span.get("type") == "tool_call"),
		"llm_calls": sum(1 for span in spans if span.get("type") == "llm_call"),
		"duration_ms": max([span.get("duration_ms", 0) for span in spans], default=0),
	}


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
	try:
		parsed = int(value)
	except (TypeError, ValueError):
		return minimum
	return max(minimum, min(maximum, parsed))


def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
	try:
		parsed = float(value)
	except (TypeError, ValueError):
		return maximum
	return max(minimum, min(maximum, parsed))


def _strict_int(value: Any, minimum: int, maximum: int, field_name: str) -> int:
	if isinstance(value, bool):
		raise ValueError(f"{field_name} must be an integer")
	try:
		parsed = int(value)
	except (TypeError, ValueError) as exc:
		raise ValueError(f"{field_name} must be an integer") from exc
	if parsed < minimum or parsed > maximum:
		raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
	return parsed


def _strict_float(value: Any, minimum: float, maximum: float, field_name: str) -> float:
	if isinstance(value, bool):
		raise ValueError(f"{field_name} must be a number")
	try:
		parsed = float(value)
	except (TypeError, ValueError) as exc:
		raise ValueError(f"{field_name} must be a number") from exc
	if parsed < minimum or parsed > maximum:
		raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
	return parsed


def _resource_name(value: Any, default: str) -> str:
	if value is None or value is True:
		return default
	if value is False:
		return ""
	return str(value)


def _log_span(span: dict[str, Any]) -> None:
	span_type = span.get("type", "unknown")
	duration = span.get("duration_ms", 0)
	trace_id = span.get("trace_id", "")
	if span_type == "tool_call":
		success = "ok" if span.get("success") else "error"
		logger.info("[trace] %s tool=%s duration=%sms round=%s trace=%s",
					success, span.get("name", ""), duration, span.get("round", 0), trace_id)
	elif span_type == "round_end":
		logger.info("[trace] round=%s tokens=%s+%s tools=%s trace=%s",
					span.get("round", 0), span.get("input_tokens", 0), span.get("output_tokens", 0),
					span.get("tool_count", 0), trace_id)
	elif span_type == "llm_call":
		logger.info("[trace] llm_call duration=%sms round=%s trace=%s",
					duration, span.get("round", 0), trace_id)
	elif span_type == "execution":
		success = "ok" if span.get("success") else "error"
		logger.info("[trace] %s execution duration=%sms tokens=%s+%s trace=%s",
					success, duration, span.get("input_tokens", 0), span.get("output_tokens", 0), trace_id)
	elif span_type == "error":
		logger.info("[trace] error name=%s trace=%s", span.get("name", ""), trace_id)
