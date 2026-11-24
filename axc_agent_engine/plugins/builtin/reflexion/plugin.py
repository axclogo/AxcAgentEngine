"""Evaluate completed rounds and inject corrections when needed. 自我反思插件 — 每轮结束后评估执行质量，发现问题注入纠正提示"""

import logging
from typing import TYPE_CHECKING

from axc_agent_engine.core.errors import LLMError
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import REFLEXION_CONFIG_SCHEMA

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext

from axc_agent_engine.plugins.builtin.reflexion.prompts import REFLECT_PROMPT

logger = logging.getLogger(__name__)


class ReflexionPlugin(BasePlugin):
	"""Evaluate each round and inject corrective guidance. 自我反思插件 — 每轮自评，发现问题注入纠正"""

	name = "reflexion"
	display_name = "自我反思"
	priority = 85
	version = "1.0.0"
	config_schema = REFLEXION_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._llm = plugin_ctx.utility_model or plugin_ctx.default_model
		self._start_after_round = config.get("start_after_round", 3)

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str = "") -> str:
		reflection = exec_ctx.get_plugin_state(self.name).get("reflection", "")
		return f"【上轮反思】{reflection}" if reflection else ""

	def transform_messages(
		self, messages: list[dict], exec_ctx: "ExecutionContext", current_message: str = ""
	) -> list[dict]:
		reflection = self.inject_context(exec_ctx)
		if not reflection:
			return messages
		index = next((i for i, msg in enumerate(messages) if msg.get("role") != "system"), len(messages))
		return [*messages[:index], {"role": "system", "content": reflection}, *messages[index:]]

	async def on_execution_end(self, exec_ctx: "ExecutionContext", result: str, error: str) -> None:
		"""Reflect on the whole run at completion. 执行结束时做整体反思"""
		if error:
			exec_ctx.get_plugin_state(self.name)["reflection"] = f"执行出错: {error}"

	async def on_round_end(
		self, exec_ctx: "ExecutionContext", user_message: str, assistant_message: str, tool_calls: list[dict]
	) -> None:
		if exec_ctx.state.current_round < self._start_after_round:
			return
		if not tool_calls:
			exec_ctx.get_plugin_state(self.name)["reflection"] = ""
			return
		calls_summary = ", ".join(tc.get("name", "unknown") for tc in tool_calls)
		prompt = REFLECT_PROMPT.format(
			calls=calls_summary,
			result=assistant_message,
		)
		try:
			content = await self._llm.ask(prompt)
			if "无问题" in content:
				exec_ctx.get_plugin_state(self.name)["reflection"] = ""
			else:
				exec_ctx.get_plugin_state(self.name)["reflection"] = content
		except (LLMError, OSError, RuntimeError, ValueError) as e:
			logger.warning(f"[reflexion] LLM reflection call failed: {e}")
			exec_ctx.get_plugin_state(self.name)["reflection"] = ""
