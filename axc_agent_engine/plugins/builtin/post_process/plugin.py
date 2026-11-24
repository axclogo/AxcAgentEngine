"""Append optional execution statistics. 按配置追加执行统计。"""

from typing import TYPE_CHECKING

from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import POST_PROCESS_CONFIG_SCHEMA

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext


class PostProcessPlugin(BasePlugin):
	"""Postprocess results with optional run statistics. 执行后处理 — 可选追加执行统计"""

	name = "post_process"
	display_name = "执行后处理"
	priority = 94
	version = "1.0.0"
	config_schema = POST_PROCESS_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._append_stats = config.get("append_stats", False)

	async def on_execution_complete(self, exec_ctx: "ExecutionContext", result: str, trace: dict) -> str:
		"""Optionally append run statistics to the result. 追加执行统计到结果末尾（可选）"""
		if not self._append_stats:
			return result
		rounds = trace["rounds"]
		input_tokens = trace["input_tokens"]
		output_tokens = trace["output_tokens"]
		stats = f"\n\n---\n执行统计: {rounds} 轮, {input_tokens}+{output_tokens} tokens"
		return result + stats
