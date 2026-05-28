"""English: This documentation describes the related engine component behavior.
中文：自我反思插件 — 每轮结束后评估执行质量，发现问题注入纠正提示"""
import logging
from typing import TYPE_CHECKING

from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import REFLEXION_CONFIG_SCHEMA

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext

from axc_agent_engine.plugins.builtin.reflexion.prompts import REFLECT_PROMPT

logger = logging.getLogger(__name__)


class ReflexionPlugin(BasePlugin):
	"""English: This documentation describes the related engine component behavior.
中文：自我反思插件 — 每轮自评，发现问题注入纠正"""
	name = "reflexion"
	display_name = "自我反思"
	priority = 85
	version = "1.0.0"
	config_schema = REFLEXION_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._llm = plugin_ctx.utility_model or plugin_ctx.default_model
		self._start_after_round = config.get("start_after_round", 3)
		self._max_len = config.get("max_reflection_len", 200)
		self._last_reflection = ""

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str = "") -> str:
		if not self._last_reflection:
			return ""
		return f"【上轮反思】{self._last_reflection}"

	async def on_execution_end(self, exec_ctx: "ExecutionContext", result: str, error: str) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：执行结束时做整体反思"""
		if error:
			self._last_reflection = f"执行出错: {error}"

	async def on_round_end(self, exec_ctx: "ExecutionContext", user_message: str,
						   assistant_message: str, tool_calls: list[dict]) -> None:
		if exec_ctx.state.current_round < self._start_after_round:
			return
		if not tool_calls:
			self._last_reflection = ""
			return
		calls_summary = ", ".join(tc.get("name", "unknown") for tc in tool_calls)
		prompt = REFLECT_PROMPT.format(
			calls=calls_summary,
			result=assistant_message,
		)
		try:
			content = await self._llm.ask(prompt)
			if "无问题" in content:
				self._last_reflection = ""
			else:
				self._last_reflection = content[:self._max_len]
		except Exception as e:
			logger.warning(f"[reflexion] LLM reflection call failed: {e}")
			self._last_reflection = ""
