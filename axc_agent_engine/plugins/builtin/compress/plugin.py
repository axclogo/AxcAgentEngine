"""Compress plugin context management.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import COMPRESS_CONFIG_SCHEMA
from axc_agent_engine.plugins.builtin.compress.context.boundary import (
	CompressionBoundary,
	CompressionBoundaryStore,
	InMemoryCompressionBoundaryStore,
	KVCompressionBoundaryStore,
)
from axc_agent_engine.plugins.builtin.compress.context.file_cache import FileReadCache
from axc_agent_engine.plugins.builtin.compress.context.normalizer import normalize_messages
from axc_agent_engine.plugins.builtin.compress.context.packer import pack_context
from axc_agent_engine.plugins.builtin.compress.context.recall import (
	fallback_recall,
	read_recall_resource,
	write_recall_resource,
)
from axc_agent_engine.plugins.builtin.compress.context.recent_window import select_recent_window
from axc_agent_engine.plugins.builtin.compress.context.summarizer import SessionSummarizer, summary_message
from axc_agent_engine.plugins.builtin.compress.context.tool_summary import (
	ToolObservation,
	ToolSummaryService,
	observation_from_output,
	tool_summaries_message,
)
from axc_agent_engine.plugins.builtin.compress.context.tool_result import (
	compact_tool_messages,
	externalize_large_tool_output,
)

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext
	from axc_agent_engine.tools.tool_output import ToolOutput


DEFAULT_DURABLE_TOOLS = {"agent_call", "knowledge_search"}
DEFAULT_DURABLE_CAPABILITIES = {"agent_call", "knowledge_search"}


class ContextCompressionPipeline:
	def __init__(self, plugin: "CompressPlugin") -> None:
		self.plugin = plugin

	def transform(self, messages: list[dict], current_message: str = "") -> list[dict]:
		plugin = self.plugin
		normalized = normalize_messages(messages)
		managed = compact_tool_messages(normalized, plugin._tool_max_inline)
		recent = select_recent_window(managed, plugin._recent_rounds)
		assembled = plugin._assemble_messages(managed, recent, current_message)
		return pack_context(assembled, plugin._max_input, plugin._reserve_output).messages


class CompressionBoundaryService:
	def __init__(self, plugin: "CompressPlugin") -> None:
		self.plugin = plugin

	async def load(self, exec_ctx: "ExecutionContext") -> None:
		plugin = self.plugin
		if not plugin._boundary_store:
			return
		agent_name = exec_ctx.state.metadata.get("agent_name", "")
		session_id = exec_ctx.state.metadata.get("session_id", "")
		key = f"{agent_name}:{session_id}"
		if not session_id or key in plugin._boundary_loaded_sessions:
			return
		boundary = await plugin._boundary_store.load(agent_name, session_id)
		plugin._boundary_loaded_sessions.add(key)
		if not boundary:
			return
		plugin._summary = boundary.summary
		plugin._round_count = max(plugin._round_count, boundary.round_count)
		plugin._conversation_buffer = list(boundary.buffer)
		plugin._file_cache.load(boundary.file_cache)
		plugin._tool_summaries = list(boundary.tool_summaries)
		plugin._durable_results = list(boundary.durable_results)

	async def save(self, exec_ctx: "ExecutionContext") -> None:
		plugin = self.plugin
		if not plugin._boundary_store:
			return
		agent_name = exec_ctx.state.metadata.get("agent_name", "")
		session_id = exec_ctx.state.metadata.get("session_id", "")
		if not session_id:
			return
		boundary = CompressionBoundary(
			agent_name=agent_name,
			session_id=session_id,
			summary=plugin._summary,
			round_count=plugin._round_count,
			compressed_rounds=max(0, plugin._round_count - len(plugin._conversation_buffer)),
			last_message_index=int(exec_ctx.state.current_round),
			buffer=list(plugin._conversation_buffer),
			file_cache=plugin._file_cache.dump(),
			tool_summaries=list(plugin._tool_summaries),
			durable_results=list(plugin._durable_results),
		)
		await plugin._boundary_store.save(boundary)


class RecallContextService:
	def __init__(self, plugin: "CompressPlugin") -> None:
		self.plugin = plugin

	def message(self, messages: list[dict[str, Any]], current_message: str) -> dict[str, str] | None:
		plugin = self.plugin
		if not _nested(plugin._recall_config, "", "enabled", True) or not current_message:
			return None
		items = plugin._read_resource_recall(current_message)
		if not items:
			items = fallback_recall(messages, current_message, plugin._recall_top_k, plugin._recall_token_limit)
		lines = [_format_recall_item(item, plugin._recall_full_threshold, plugin._recall_compressed_threshold) for item in items]
		lines = [line for line in lines if line]
		return {"role": "system", "content": "[召回上下文]\n" + "\n".join(lines)} if lines else None

	async def write(self, user_message: str, assistant_message: str, exec_ctx: "ExecutionContext") -> None:
		plugin = self.plugin
		resource_name = plugin._recall_config.get("resource", "")
		resource = plugin._plugin_ctx.resources.get(resource_name) if resource_name else None
		texts = [text for text in (user_message, assistant_message) if text]
		metadata = [{"round": plugin._round_count, "agent": exec_ctx.state.metadata.get("agent_name", "")} for _ in texts]
		await write_recall_resource(resource, texts, metadata)


class ToolSummaryCoordinator:
	def __init__(self, plugin: "CompressPlugin") -> None:
		self.plugin = plugin

	async def summarize(self) -> None:
		plugin = self.plugin
		if not plugin._tool_summary_enabled or not plugin._pending_tool_observations:
			plugin._pending_tool_observations.clear()
			return
		summary = await plugin._tool_summary_service.summarize(plugin._plugin_ctx.utility_model, plugin._pending_tool_observations)
		plugin._pending_tool_observations.clear()
		if not summary:
			return
		plugin._tool_summaries.append(summary)
		if plugin._tool_summary_keep > 0 and len(plugin._tool_summaries) > plugin._tool_summary_keep:
			plugin._tool_summaries = plugin._tool_summaries[-plugin._tool_summary_keep:]
		plugin._conversation_buffer.append(f"Tool summary: {summary}")


class CompressPlugin(BasePlugin):
	"""Context management plugin exposed under the historical name compress.
中文：此文档说明相关引擎组件的行为。"""

	name = "compress"
	display_name = "上下文治理"
	priority = 80
	version = "2.0.0"
	config_schema = COMPRESS_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		super().initialize(config, plugin_ctx)
		self._config = config
		self._tool_max_inline = _nested(config, "tool_result", "max_inline_tokens", config.get("snip_threshold", 1200))
		self._artifact_threshold = _nested(config, "tool_result", "artifact_threshold_tokens", 4000)
		self._recent_rounds = _nested(config, "recent_window", "rounds", config.get("micro_compact_keep_recent", 4))
		self._max_input = _nested(config, "context_window", "max_input_tokens", 24000)
		self._reserve_output = _nested(config, "context_window", "reserve_output_tokens", 4000)
		self._summary_after = _nested(config, "summary", "after_rounds", config.get("summary_after_rounds", 8))
		self._summary_keep_recent = _nested(config, "summary", "keep_recent_rounds", config.get("summary_keep_recent", 3))
		self._summary_enabled = _nested(config, "summary", "enabled", True)
		self._file_cache = FileReadCache(
			max_files=_nested(config, "file_restore", "max_files", 5),
			max_chars_per_file=_nested(config, "file_restore", "max_chars_per_file", 4000),
			max_total_chars=_nested(config, "file_restore", "max_total_chars", 12000),
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
		self._durable_tool_names.update(str(name) for name in _nested(durable_config, "", "names", []))
		self._durable_capabilities = set(DEFAULT_DURABLE_CAPABILITIES)
		self._durable_capabilities.update(str(name) for name in _nested(durable_config, "", "capabilities", []))
		self._durable_results: list[str] = []
		self._durable_keep = int(_nested(durable_config, "", "keep", 12))
		self._durable_max_chars = int(_nested(durable_config, "", "max_chars", 4000))
		self._recall_config = config.get("recall", {})
		self._boundary_enabled = _nested(config, "boundary", "enabled", True)
		self._boundary_resource = _nested(config, "boundary", "resource", "")
		self._boundary_store: CompressionBoundaryStore | None = None
		self._boundary_loaded_sessions: set[str] = set()
		self._round_count = 0
		self._conversation_buffer: list[str] = []
		self._summary = ""
		self._summarizer = SessionSummarizer(
			max_tokens=_nested(config, "summary", "max_tokens", config.get("summary_max_length", 800)),
			max_failures=_nested(config, "summary", "max_failures", config.get("max_compact_failures", 3)),
		)
		self._compact_failures = 0
		self._compact_broken = False
		if self._boundary_enabled:
			resource = plugin_ctx.resources.get(self._boundary_resource) if self._boundary_resource else None
			if resource and hasattr(resource, "load") and hasattr(resource, "save"):
				self._boundary_store = resource
			elif plugin_ctx.kv_store:
				self._boundary_store = KVCompressionBoundaryStore(plugin_ctx.kv_store)
			else:
				self._boundary_store = InMemoryCompressionBoundaryStore()
		self._pipeline = ContextCompressionPipeline(self)
		self._boundary_service = CompressionBoundaryService(self)
		self._recall_service = RecallContextService(self)
		self._tool_summary_coordinator = ToolSummaryCoordinator(self)

	async def on_execution_start(self, exec_ctx: "ExecutionContext") -> None:
		await self._load_boundary(exec_ctx)

	def transform_messages(self, messages: list[dict], exec_ctx: "ExecutionContext",
						   current_message: str = "") -> list[dict]:
		return self._pipeline.transform(messages, current_message)

	async def post_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
							 arguments: dict, result: "ToolOutput", duration_ms: int) -> "ToolOutput":
		self._file_cache.update_from_tool(tool_name, arguments, result)
		if self._tool_summary_enabled:
			self._pending_tool_observations.append(observation_from_output(tool_name, arguments, result, duration_ms))
		self._record_durable_result(tool_name, result)
		return await externalize_large_tool_output(
			result,
			exec_ctx.services.result_store,
			self._artifact_threshold,
		)

	async def on_round_end(self, exec_ctx: "ExecutionContext", user_message: str,
						   assistant_message: str, tool_calls: list[dict]) -> None:
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
		return self._recall_service.message(messages, current_message)

	def _read_resource_recall(self, current_message: str):
		resource_name = self._recall_config.get("resource", "")
		resource = self._plugin_ctx.resources.get(resource_name) if resource_name else None
		return read_recall_resource(resource, current_message, self._recall_top_k)

	async def _write_recall(self, user_message: str, assistant_message: str, exec_ctx: "ExecutionContext") -> None:
		await self._recall_service.write(user_message, assistant_message, exec_ctx)

	async def _maybe_summarize(self) -> None:
		if not self._summary_enabled or self._round_count < self._summary_after or self._summary:
			return
		self._summary = await self._summarizer.generate(self._plugin_ctx.utility_model, self._conversation_buffer)
		self._compact_failures = self._summarizer.state.failures
		self._compact_broken = self._summarizer.state.broken
		if self._summary:
			self._conversation_buffer.clear()

	async def _summarize_tools(self) -> None:
		await self._tool_summary_coordinator.summarize()

	async def _load_boundary(self, exec_ctx: "ExecutionContext") -> None:
		await self._boundary_service.load(exec_ctx)

	async def _save_boundary(self, exec_ctx: "ExecutionContext") -> None:
		await self._boundary_service.save(exec_ctx)

	def _buffer_round(self, user_message: str, assistant_message: str, tool_calls: list[dict]) -> None:
		if user_message:
			self._conversation_buffer.append(f"User: {user_message}")
		if assistant_message:
			self._conversation_buffer.append(f"Assistant: {assistant_message}")
		for call in tool_calls:
			self._conversation_buffer.append(f"Tool call: {call.get('name', '')}")

	def _record_durable_result(self, tool_name: str, result: "ToolOutput") -> None:
		if not result or result.is_error or not self._is_durable_tool(tool_name, result):
			return
		content = result.durable_summary(self._durable_max_chars) or result.context_view(self._durable_max_chars)
		if not content:
			return
		entry = f"{tool_name}: {content}"
		self._durable_results.append(entry)
		self._conversation_buffer.append(f"Tool result: {entry}")
		if self._durable_keep > 0 and len(self._durable_results) > self._durable_keep:
			self._durable_results = self._durable_results[-self._durable_keep:]

	def _is_durable_tool(self, tool_name: str, result: "ToolOutput") -> bool:
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
	if not section:
		return config.get(key, default)
	value = config.get(section, {})
	return value.get(key, default) if isinstance(value, dict) else default


def _format_recall_item(item, full_threshold: float, compressed_threshold: float) -> str:
	if item.score >= full_threshold:
		return f"- {item.text}"
	if item.score >= compressed_threshold:
		return f"- {item.text[:300]} [压缩召回]"
	return ""


def _durable_results_message(results: list[str]) -> dict[str, str] | None:
	lines = [item.strip() for item in results if item and item.strip()]
	if not lines:
		return None
	return {"role": "system", "content": "[持久工具结果]\n" + "\n\n".join(f"- {item}" for item in lines)}
