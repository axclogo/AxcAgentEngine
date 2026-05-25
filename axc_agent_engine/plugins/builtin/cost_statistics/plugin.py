"""CostStatistics plugin: token usage accounting only."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.tools.tool_output import ToolOutput

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext

logger = logging.getLogger(__name__)


def _to_int(value: Any, default: int = 0) -> int:
	try:
		return max(0, int(value))
	except (TypeError, ValueError):
		return default


class CostStatisticsPlugin(BasePlugin):
	name = "cost_statistics"
	display_name = "成本统计"
	priority = 90
	version = "2.0.0"

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		super().initialize(config, plugin_ctx)
		self._model = str(config.get("model", "") or "")
		if not self._model and plugin_ctx:
			self._model = plugin_ctx.model_name

	def get_tools(self) -> list[ToolDefinition]:
		return [ToolDefinition(
			name="cost_statistics",
			description="返回当前执行的 token 用量统计。该工具只读，不会阻断、降级或计费。",
			parameters={"type": "object", "properties": {}},
			execute=self._cost_statistics,
			is_read_only=True,
			capability="",
			risk_level="safe",
		)]

	def should_stop(self, exec_ctx: "ExecutionContext") -> tuple[bool, str]:
		"""Token accounting never controls execution flow."""
		self._sync_summary(exec_ctx)
		return False, ""

	async def post_llm_call(self, exec_ctx: "ExecutionContext", messages: list[dict],
							response: dict, duration_ms: int) -> None:
		"""Record one LLM call token sample."""
		usage = response.get("usage", {})
		input_tokens = _to_int(usage.get("input_tokens", 0))
		output_tokens = _to_int(usage.get("output_tokens", 0))
		if input_tokens == 0 and output_tokens == 0:
			self._sync_summary(exec_ctx)
			return
		model = self._current_model(exec_ctx)
		state = self._state(exec_ctx)
		state["llm_calls"] = _to_int(state.get("llm_calls", 0)) + 1
		state["input_tokens"] = _to_int(state.get("input_tokens", 0)) + input_tokens
		state["output_tokens"] = _to_int(state.get("output_tokens", 0)) + output_tokens
		by_model = state.setdefault("by_model", {})
		model_stats = by_model.setdefault(model, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0})
		model_stats["calls"] = _to_int(model_stats.get("calls", 0)) + 1
		model_stats["input_tokens"] = _to_int(model_stats.get("input_tokens", 0)) + input_tokens
		model_stats["output_tokens"] = _to_int(model_stats.get("output_tokens", 0)) + output_tokens
		model_stats["total_tokens"] = _to_int(model_stats.get("total_tokens", 0)) + input_tokens + output_tokens
		self._sync_summary(exec_ctx)
		logger.debug(
			"[tokens] llm_call model=%s tokens=%s+%s total=%s",
			model, input_tokens, output_tokens, state["input_tokens"] + state["output_tokens"],
		)

	async def post_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
							 arguments: dict, result: "ToolOutput", duration_ms: int) -> "ToolOutput":
		"""Record tool call count for usage observability."""
		state = self._state(exec_ctx)
		state["tool_calls"] = _to_int(state.get("tool_calls", 0)) + 1
		by_tool = state.setdefault("by_tool", {})
		tool_stats = by_tool.setdefault(tool_name, {"calls": 0})
		tool_stats["calls"] = _to_int(tool_stats.get("calls", 0)) + 1
		self._sync_summary(exec_ctx)
		return result

	async def _cost_statistics(self, args: dict, context: dict) -> ToolOutput:
		exec_ctx = context.get("exec_ctx")
		if not exec_ctx:
			return ToolOutput.error("cost_statistics requires execution context")
		summary = self._sync_summary(exec_ctx)
		return ToolOutput.json_output(summary, summary=f"当前 token 用量：{summary['total_tokens']}")

	def _state(self, exec_ctx: "ExecutionContext") -> dict[str, Any]:
		return exec_ctx.get_plugin_state(self.name, lambda: {
			"llm_calls": 0,
			"tool_calls": 0,
			"input_tokens": 0,
			"output_tokens": 0,
			"by_model": {},
			"by_tool": {},
		})

	def _sync_summary(self, exec_ctx: "ExecutionContext") -> dict[str, Any]:
		state = self._state(exec_ctx)
		input_tokens = _to_int(state.get("input_tokens", 0))
		output_tokens = _to_int(state.get("output_tokens", 0))
		summary = {
			"input_tokens": input_tokens,
			"output_tokens": output_tokens,
			"total_tokens": input_tokens + output_tokens,
			"llm_calls": _to_int(state.get("llm_calls", 0)),
			"tool_calls": _to_int(state.get("tool_calls", 0)),
			"model": self._current_model(exec_ctx),
			"by_model": state.get("by_model", {}),
			"by_tool": state.get("by_tool", {}),
		}
		exec_ctx.state.metadata[self.name] = summary
		return summary

	def _current_model(self, exec_ctx: "ExecutionContext") -> str:
		model_info = getattr(exec_ctx.runtime, "model_info", None)
		if model_info and model_info.active:
			return model_info.active
		metadata_model = exec_ctx.state.metadata.get("model", {})
		if isinstance(metadata_model, dict) and metadata_model.get("active"):
			return str(metadata_model["active"])
		return self._model or "default"
