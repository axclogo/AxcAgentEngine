"""自定义插件示例 — 展示如何开发外部插件

这个插件在每轮结束后打印统计信息，并提供一个自定义工具。
用户可以参考此文件开发自己的插件。
"""
import json
from typing import Any

from axc_agent_engine import BasePlugin, ToolDefinition, ToolOutput


class StatsPlugin(BasePlugin):
	"""统计插件 — 记录每轮的工具调用次数和 token 消耗"""
	name = "stats"
	display_name = "统计插件"
	priority = 50
	version = "1.0.0"

	def initialize(self, config: dict, ctx: Any) -> None:
		self._total_tool_calls = 0
		self._greeting = config.get("greeting", "📊")

	def get_tools(self) -> list[ToolDefinition]:
		return [ToolDefinition(
			name="get_stats",
			description="获取当前执行的统计信息",
			parameters={"type": "object", "properties": {}},
			is_read_only=True,
			execute=self._tool_get_stats,
		)]

	async def on_round_end(self, ctx: Any, user_message: str,
						   assistant_message: str, tool_calls: list) -> None:
		self._total_tool_calls += len(tool_calls)
		round_num = getattr(ctx.state, "current_round", 0)
		usage = getattr(ctx.state, "usage", None)
		total_tokens = getattr(usage, "total_tokens", 0) if usage else 0
		print(f"{self._greeting} 第 {round_num} 轮: "
			  f"工具调用 {len(tool_calls)} 次, "
			  f"累计 tokens {total_tokens}")

	async def _tool_get_stats(self, args: dict, context: dict) -> ToolOutput:
		return ToolOutput.text(json.dumps({
			"total_tool_calls": self._total_tool_calls,
		}))
