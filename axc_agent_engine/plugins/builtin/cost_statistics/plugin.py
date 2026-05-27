"""CostStatistics plugin: token usage accounting only.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import COST_STATISTICS_CONFIG_SCHEMA
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.tools.tool_output import ToolOutput

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext

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
	config_schema = COST_STATISTICS_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		super().initialize(config, plugin_ctx)

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
		"""Token accounting never controls execution flow.
中文：此文档说明相关引擎组件的行为。"""
		self._sync_summary(exec_ctx)
		return False, ""

	async def post_llm_call(self, exec_ctx: "ExecutionContext", messages: list[dict],
							response: dict, duration_ms: int) -> None:
		"""Record one LLM call token sample.
中文：此文档说明相关引擎组件的行为。"""
		usage = response.get("usage", {})
		input_tokens = _to_int(usage.get("input_tokens", 0))
		output_tokens = _to_int(usage.get("output_tokens", 0))
		if input_tokens == 0 and output_tokens == 0:
			self._sync_summary(exec_ctx)
			return
		state = self._state(exec_ctx)
		state["input_tokens"] = _to_int(state.get("input_tokens", 0)) + input_tokens
		state["output_tokens"] = _to_int(state.get("output_tokens", 0)) + output_tokens
		self._sync_summary(exec_ctx)

	async def post_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
							 arguments: dict, result: "ToolOutput", duration_ms: int) -> "ToolOutput":
		"""Record tool call count for usage observability.
中文：此文档说明相关引擎组件的行为。"""
		return result

	async def _cost_statistics(self, args: dict, context: dict) -> ToolOutput:
		exec_ctx = context.get("exec_ctx")
		if not exec_ctx:
			return ToolOutput.error("cost_statistics requires execution context")
		summary = self._sync_summary(exec_ctx)
		return ToolOutput.json_output(summary, summary=f"当前 token 用量：{summary['total_tokens']}")

	def _state(self, exec_ctx: "ExecutionContext") -> dict[str, Any]:
		return exec_ctx.get_plugin_state(self.name, lambda: {
			"input_tokens": 0,
			"output_tokens": 0,
		})

	def _sync_summary(self, exec_ctx: "ExecutionContext") -> dict[str, Any]:
		state = self._state(exec_ctx)
		input_tokens = _to_int(state.get("input_tokens", 0))
		output_tokens = _to_int(state.get("output_tokens", 0))
		summary = {
			"input_tokens": input_tokens,
			"output_tokens": output_tokens,
			"total_tokens": input_tokens + output_tokens,
		}
		exec_ctx.state.metadata[self.name] = summary
		return summary
