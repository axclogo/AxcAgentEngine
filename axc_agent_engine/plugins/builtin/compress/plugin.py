"""Compress plugin context management. 压缩插件的上下文管理。"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.compress.context.boundary import (
	CompressionBoundary,
	CompressionBoundaryStore,
	InMemoryCompressionBoundaryStore,
	KVCompressionBoundaryStore,
)
from axc_agent_engine.plugins.builtin.compress.context.file_cache import FileReadCache
from axc_agent_engine.plugins.builtin.compress.context.models import ContextSummary
from axc_agent_engine.plugins.builtin.compress.context.normalizer import normalize_messages
from axc_agent_engine.plugins.builtin.compress.context.packer import pack_context
from axc_agent_engine.plugins.builtin.compress.context.recall import (
	fallback_recall,
	read_recall_resource,
	write_recall_resource,
)
from axc_agent_engine.plugins.builtin.compress.context.recent_window import select_recent_window
from axc_agent_engine.plugins.builtin.compress.context.summarizer import SessionSummarizer, summary_message
from axc_agent_engine.plugins.builtin.compress.context.tool_result import (
	externalize_large_tool_output,
)
from axc_agent_engine.plugins.builtin.compress.context.tool_summary import (
	ToolObservation,
	ToolSummaryService,
	observation_from_output,
	tool_summaries_message,
)
from axc_agent_engine.plugins.builtin.config_schemas import COMPRESS_CONFIG_SCHEMA

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext
	from axc_agent_engine.tools.tool_output import ToolOutput


DEFAULT_DURABLE_TOOLS = {"agent_call", "knowledge_search"}
DEFAULT_DURABLE_CAPABILITIES = {"agent_call", "knowledge_search"}


class CompressPlugin(BasePlugin):
	"""Context management plugin exposed under the historical name compress. 以 compress 名称提供的上下文管理插件。"""

	name = "compress"
	display_name = "上下文治理"
	priority = 80
	version = "2.0.0"
	config_schema = COMPRESS_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: PluginContext) -> None:
		super().initialize(config, plugin_ctx)
		self._config = config
		self.supports_session_fork = not (
			_nested(config, "recall", "enabled", True) and _nested(config, "recall", "resource", "")
		)
		self._boundary_store: CompressionBoundaryStore | None = None
		if _nested(config, "boundary", "enabled", True):
			resource_name = _nested(config, "boundary", "resource", "")
			if resource_name:
				self._boundary_store = plugin_ctx.resources.require(resource_name, CompressionBoundaryStore)
			elif plugin_ctx.kv_store:
				self._boundary_store = KVCompressionBoundaryStore(plugin_ctx.kv_store)
			else:
				self._boundary_store = InMemoryCompressionBoundaryStore()

	def capture_session(self, ctx: ExecutionContext) -> dict:
		return self._run(ctx).capture_session(ctx)

	async def restore_session(self, ctx: ExecutionContext, state: dict) -> None:
		await self._run(ctx).restore_session(ctx, state)

	async def on_session_reset(self, agent_name: str, session_id: str) -> None:
		store = self._boundary_store
		if store is None:
			return
		if session_id:
			await store.delete(agent_name, session_id)
		else:
			await store.clear(agent_name)

	def _run(self, ctx: ExecutionContext) -> CompressionRun:
		def create_run() -> CompressionRun:
			metadata = ctx.state.metadata
			scope = {"agent": metadata.get("agent_name", ""), "session": metadata.get("session_id", "")}
			if not scope["session"]:
				scope["run"] = metadata.get("run_id") or uuid4().hex
			return CompressionRun(self._config, self._plugin_ctx, self._boundary_store, scope)

		return ctx.get_plugin_state(self.name, create_run)

	async def on_execution_start(self, exec_ctx: ExecutionContext) -> None:
		await self._run(exec_ctx).load_boundary(exec_ctx)

	def transform_messages(
		self, messages: list[dict], exec_ctx: ExecutionContext, current_message: str = ""
	) -> list[dict]:
		return self._run(exec_ctx).transform(messages, current_message)

	async def post_tool_call(
		self, exec_ctx: ExecutionContext, tool_name: str, arguments: dict, result: ToolOutput, duration_ms: int
	) -> ToolOutput:
		return await self._run(exec_ctx).post_tool_call(exec_ctx, tool_name, arguments, result, duration_ms)

	async def on_round_end(
		self, exec_ctx: ExecutionContext, user_message: str, assistant_message: str, tool_calls: list[dict]
	) -> None:
		await self._run(exec_ctx).on_round_end(exec_ctx, user_message, assistant_message, tool_calls)


class CompressionRun:
	def __init__(
		self, config: dict, plugin_ctx: PluginContext, store: CompressionBoundaryStore | None, scope: dict
	) -> None:
		self._scope = scope
		self._boundary_store = store
		self._plugin_ctx = plugin_ctx
		self._artifact_threshold = _nested(config, "tool_result", "artifact_threshold_tokens", 4000)
		self._recent_rounds = _nested(config, "recent_window", "rounds", 4)
		self._max_input = _nested(config, "context_window", "max_input_tokens", 24000)
		self._reserve_output = _nested(config, "context_window", "reserve_output_tokens", 4000)
		self._summary_after = _nested(config, "summary", "after_rounds", 8)
		self._summary_enabled = _nested(config, "summary", "enabled", True)
		self._file_cache = FileReadCache(
			max_files=_nested(config, "file_restore", "max_files", 5),
		)
		self._file_restore_enabled = _nested(config, "file_restore", "enabled", True)
		self._tool_summary_enabled = _nested(config, "tool_summary", "enabled", False)
		self._tool_summary_keep = int(_nested(config, "tool_summary", "keep", 8))
		self._tool_summary_service = ToolSummaryService(
			max_chars=_nested(config, "tool_summary", "max_chars", 1200),
			max_observations=_nested(config, "tool_summary", "max_observations", 20),
		)
		self._pending_tool_observations: list[ToolObservation] = []
		self._tool_summaries: list[str] = []
		durable_config = config.get("durable_tools", {})
		self._durable_tool_names = set(DEFAULT_DURABLE_TOOLS)
		self._durable_tool_names.update(durable_config.get("names", []))
		self._durable_capabilities = set(DEFAULT_DURABLE_CAPABILITIES)
		self._durable_capabilities.update(durable_config.get("capabilities", []))
		self._durable_results: list[str] = []
		self._durable_keep = int(durable_config.get("keep", 12))
		self._durable_max_chars = int(durable_config.get("max_chars", 4000))
		self._recall_config = config.get("recall", {})
		self._round_count = 0
		self._conversation_buffer: list[str] = []
		self._summary = ""
		self._summarizer = SessionSummarizer(
			max_tokens=_nested(config, "summary", "max_tokens", 800),
			max_failures=_nested(config, "summary", "max_failures", 3),
		)

	def capture_session(self, ctx: ExecutionContext) -> dict:
		return {
			"boundary": self._boundary(ctx).to_dict(),
			"summarizer": asdict(self._summarizer.state),
			"pending": [asdict(item) for item in self._pending_tool_observations],
		}

	async def restore_session(self, ctx: ExecutionContext, state: dict) -> None:
		self._apply_boundary(CompressionBoundary.from_dict(state["boundary"]))
		self._summarizer.state = ContextSummary(**state["summarizer"])
		self._pending_tool_observations = [ToolObservation(**item) for item in state["pending"]]
		await self._save_boundary(ctx)

	def transform(self, messages: list[dict], current_message: str = "") -> list[dict]:
		normalized = normalize_messages(messages)
		recent = select_recent_window(normalized, self._recent_rounds)
		assembled = self._assemble_messages(normalized, recent, current_message)
		return pack_context(assembled, self._max_input, self._reserve_output).messages

	async def post_tool_call(
		self, exec_ctx: ExecutionContext, tool_name: str, arguments: dict, result: ToolOutput, duration_ms: int
	) -> ToolOutput:
		self._file_cache.update_from_tool(tool_name, arguments, result)
		if self._tool_summary_enabled:
			self._pending_tool_observations.append(observation_from_output(tool_name, arguments, result, duration_ms))
		self._record_durable_result(tool_name, result)
		return await externalize_large_tool_output(
			result,
			exec_ctx.services.artifact_store,
			self._artifact_threshold,
		)

	async def on_round_end(
		self, exec_ctx: ExecutionContext, user_message: str, assistant_message: str, tool_calls: list[dict]
	) -> None:
		self._round_count += 1
		self._buffer_round(user_message, assistant_message, tool_calls)
		await self._write_recall(user_message, assistant_message, exec_ctx)
		await self._summarize_tools()
		await self._maybe_summarize()
		await self._save_boundary(exec_ctx)

	def _assemble_messages(
		self,
		all_messages: list[dict[str, Any]],
		recent: list[dict[str, Any]],
		current_message: str,
	) -> list[dict[str, Any]]:
		system = [m for m in recent if m.get("role") == "system"]
		body = [m for m in recent if m.get("role") != "system"]
		extra = []
		summary = summary_message(self._summary)
		if summary:
			extra.append(summary)
		tool_summary = tool_summaries_message(self._tool_summaries)
		if tool_summary:
			extra.append(tool_summary)
		durable_summary = _durable_results_message(self._durable_results)
		if durable_summary:
			extra.append(durable_summary)
		if self._summary and self._file_restore_enabled:
			file_cache = self._file_cache.message()
			if file_cache:
				extra.append(file_cache)
		recall = self._recall_message(all_messages, current_message)
		if recall:
			extra.append(recall)
		return system + extra + body

	def _recall_message(self, messages: list[dict[str, Any]], current_message: str) -> dict[str, str] | None:
		if not self._recall_config.get("enabled", True) or not current_message:
			return None
		items = self._read_resource_recall(current_message)
		if not items:
			items = fallback_recall(messages, current_message, self._recall_top_k, self._recall_token_limit)
		lines = [
			_format_recall_item(item, self._recall_full_threshold, self._recall_compressed_threshold) for item in items
		]
		lines = [line for line in lines if line]
		return {"role": "system", "content": "[召回上下文]\n" + "\n".join(lines)} if lines else None

	def _read_resource_recall(self, current_message: str):
		resource_name = self._recall_config.get("resource", "")
		resource = self._plugin_ctx.resources.get(resource_name) if resource_name else None
		return [
			item
			for item in read_recall_resource(resource, current_message, self._recall_top_k)
			if item.metadata.get("scope") == self._scope
		]

	async def _write_recall(self, user_message: str, assistant_message: str, exec_ctx: ExecutionContext) -> None:
		resource_name = self._recall_config.get("resource", "")
		resource = self._plugin_ctx.resources.get(resource_name) if resource_name else None
		texts = [text for text in (user_message, assistant_message) if text]
		metadata = [{"round": self._round_count, "scope": self._scope} for _ in texts]
		await write_recall_resource(resource, texts, metadata)

	async def _maybe_summarize(self) -> None:
		if not self._summary_enabled or self._round_count < self._summary_after or self._summary:
			return
		self._summary = await self._summarizer.generate(self._plugin_ctx.utility_model, self._conversation_buffer)
		if self._summary:
			self._conversation_buffer.clear()

	async def _summarize_tools(self) -> None:
		if not self._tool_summary_enabled or not self._pending_tool_observations:
			self._pending_tool_observations.clear()
			return
		summary = await self._tool_summary_service.summarize(
			self._plugin_ctx.utility_model, self._pending_tool_observations
		)
		self._pending_tool_observations.clear()
		if not summary:
			return
		self._tool_summaries.append(summary)
		if self._tool_summary_keep > 0 and len(self._tool_summaries) > self._tool_summary_keep:
			self._tool_summaries = self._tool_summaries[-self._tool_summary_keep :]
		self._conversation_buffer.append(f"Tool summary: {summary}")

	async def load_boundary(self, exec_ctx: ExecutionContext) -> None:
		if not self._boundary_store or exec_ctx.runtime.cancel_source is not None:
			return
		agent_name = exec_ctx.state.metadata.get("agent_name", "")
		session_id = exec_ctx.state.metadata.get("session_id", "")
		if not session_id:
			return
		boundary = await self._boundary_store.load(agent_name, session_id)
		if boundary:
			self._apply_boundary(boundary)

	def _apply_boundary(self, boundary: CompressionBoundary) -> None:
		self._summary = boundary.summary
		self._round_count = max(self._round_count, boundary.round_count)
		self._conversation_buffer = list(boundary.buffer)
		self._file_cache.load(boundary.file_cache)
		self._tool_summaries = list(boundary.tool_summaries)
		self._durable_results = list(boundary.durable_results)

	async def _save_boundary(self, exec_ctx: ExecutionContext) -> None:
		if not self._boundary_store or exec_ctx.runtime.cancel_source is not None:
			return
		if not exec_ctx.state.metadata.get("session_id"):
			return
		await self._boundary_store.save(self._boundary(exec_ctx))

	def _boundary(self, exec_ctx: ExecutionContext) -> CompressionBoundary:
		return CompressionBoundary(
			agent_name=exec_ctx.state.metadata.get("agent_name", ""),
			session_id=exec_ctx.state.metadata.get("session_id", ""),
			summary=self._summary,
			round_count=self._round_count,
			compressed_rounds=max(0, self._round_count - len(self._conversation_buffer)),
			last_message_index=int(exec_ctx.state.current_round),
			buffer=list(self._conversation_buffer),
			file_cache=self._file_cache.dump(),
			tool_summaries=list(self._tool_summaries),
			durable_results=list(self._durable_results),
		)

	def _buffer_round(self, user_message: str, assistant_message: str, tool_calls: list[dict]) -> None:
		if user_message:
			self._conversation_buffer.append(f"User: {user_message}")
		if assistant_message:
			self._conversation_buffer.append(f"Assistant: {assistant_message}")
		for call in tool_calls:
			self._conversation_buffer.append(f"Tool call: {call.get('name', '')}")

	def _record_durable_result(self, tool_name: str, result: ToolOutput) -> None:
		if result.is_error or not self._is_durable_tool(tool_name, result):
			return
		content = result.durable_summary(self._durable_max_chars) or result.context_view(self._durable_max_chars)
		if not content:
			return
		entry = f"{tool_name}: {content}"
		self._durable_results.append(entry)
		self._conversation_buffer.append(f"Tool result: {entry}")
		if self._durable_keep > 0 and len(self._durable_results) > self._durable_keep:
			self._durable_results = self._durable_results[-self._durable_keep :]

	def _is_durable_tool(self, tool_name: str, result: ToolOutput) -> bool:
		if result.is_durable() or tool_name in self._durable_tool_names:
			return True
		capability = str(result.metadata.get("capability", ""))
		return bool(capability and capability in self._durable_capabilities)

	@property
	def _recall_top_k(self) -> int:
		return int(self._recall_config.get("top_k", 12))

	@property
	def _recall_token_limit(self) -> int:
		return int(self._recall_config.get("token_limit", 4000))

	@property
	def _recall_full_threshold(self) -> float:
		return float(self._recall_config.get("full_threshold", 0.72))

	@property
	def _recall_compressed_threshold(self) -> float:
		return float(self._recall_config.get("compressed_threshold", 0.35))


def _nested(config: dict, section: str, key: str, default: Any) -> Any:
	return config.get(section, {}).get(key, default)


def _format_recall_item(item, full_threshold: float, compressed_threshold: float) -> str:
	if item.score >= full_threshold:
		return f"- {item.text}"
	if item.score >= compressed_threshold:
		return f"- {item.text} [压缩召回]"
	return ""


def _durable_results_message(results: list[str]) -> dict[str, str] | None:
	lines = [item.strip() for item in results if item and item.strip()]
	if not lines:
		return None
	return {"role": "system", "content": "[持久工具结果]\n" + "\n\n".join(f"- {item}" for item in lines)}
