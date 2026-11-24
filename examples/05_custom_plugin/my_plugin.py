"""External statistics plugin. 外部统计插件示例。"""

from axc_agent_engine import BasePlugin, ToolDefinition, ToolOutput
from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.plugins.config_schema import config_field, config_schema
from axc_agent_engine.plugins.context import PluginContext


class StatsPlugin(BasePlugin):
	"""Report per-run usage. 记录每次运行的工具调用次数与用量。"""

	name = "stats"
	display_name = "统计插件"
	priority = 50
	version = "1.0.0"

	config_schema = config_schema(
		name,
		display_name,
		"报告每次运行的统计信息。",
		[config_field("greeting", "前缀", "string", "输出前缀。", default="📊")],
	)

	def initialize(self, config: dict, ctx: PluginContext) -> None:
		super().initialize(config, ctx)
		self._greeting = config.get("greeting", "📊")

	def get_tools(self) -> list[ToolDefinition]:
		return [
			ToolDefinition(
				name="get_stats",
				description="获取当前执行的统计信息",
				parameters={"type": "object", "properties": {}},
				is_read_only=True,
				execute=self._tool_get_stats,
			)
		]

	async def on_round_end(
		self, ctx: ExecutionContext, user_message: str, assistant_message: str, tool_calls: list
	) -> None:
		state = ctx.get_plugin_state(self.name)
		state["total_tool_calls"] = state.get("total_tool_calls", 0) + len(tool_calls)
		total_tokens = ctx.state.total_input_tokens + ctx.state.total_output_tokens
		print(
			f"{self._greeting} 第 {ctx.state.current_round} 轮: 工具调用 {len(tool_calls)} 次, 累计 tokens {total_tokens}"
		)

	async def _tool_get_stats(self, args: dict, context: dict) -> ToolOutput:
		state = context["exec_ctx"].get_plugin_state(self.name)
		return ToolOutput.json_output({"total_tool_calls": state.get("total_tool_calls", 0)})
