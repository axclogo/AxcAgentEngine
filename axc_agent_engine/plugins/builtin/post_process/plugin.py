"""PostProcess 插件 — 执行后处理（工具摘要、统计汇总）"""
import logging
from typing import TYPE_CHECKING

from axc_agent_engine.plugins.base import BasePlugin

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext

logger = logging.getLogger(__name__)


class PostProcessPlugin(BasePlugin):
	"""English: This documentation describes the related engine component behavior.
中文：执行后处理 — 可选追加执行统计"""
	name = "post_process"
	display_name = "执行后处理"
	priority = 99
	version = "1.0.0"

	def initialize(self, config: dict, plugin_ctx: "PluginContext" = None) -> None:
		self._append_stats = config.get("append_stats", False)

	async def on_execution_complete(self, exec_ctx: "ExecutionContext", result: str, trace: dict) -> str:
		"""English: This documentation describes the related engine component behavior.
中文：追加执行统计到结果末尾（可选）"""
		if not self._append_stats:
			return result
		rounds = trace.get("rounds", 0)
		input_tokens = trace.get("input_tokens", 0)
		output_tokens = trace.get("output_tokens", 0)
		stats = f"\n\n---\n执行统计: {rounds} 轮, {input_tokens}+{output_tokens} tokens"
		return result + stats
