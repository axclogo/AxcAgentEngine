"""LLMBasedStop — LLM 判断终止条件基类"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext

logger = logging.getLogger(__name__)


class LLMBasedStop:
	"""基于 LLM 判断的终止条件基类，子类只需定义 prompt 和判断逻辑"""

	prompt_template: str = ""
	success_keyword: str = ""
	success_reason: str = ""
	recent_limit: int = 6

	def __init__(self, llm_client: Any | None = None, check_interval: int = 3,
				 max_rounds: int = 20) -> None:
		self._llm_client = llm_client
		self._check_interval = check_interval
		self._max_rounds = max_rounds

	async def should_stop(self, ctx: "SharedContext", round_num: int) -> tuple[bool, str]:
		if round_num >= self._max_rounds:
			return True, f"已达到最大轮次 {self._max_rounds}"
		if round_num == 0 or round_num % self._check_interval != 0:
			return False, ""
		if not self._llm_client:
			return False, ""
		try:
			content = ctx.get_recent_contents(limit=self.recent_limit)
			prompt = self.prompt_template.format(content=content)
			answer = await self._llm_client.ask(prompt)
			return self._evaluate(answer.strip(), ctx, round_num)
		except Exception as e:
			logger.warning(f"[{self.__class__.__name__}] LLM call failed: {e}")
		return False, ""

	def _evaluate(self, answer: str, ctx: "SharedContext", round_num: int) -> tuple[bool, str]:
		"""评估 LLM 回复，子类可覆盖"""
		first_line = answer.split("\n")[0].strip().upper()
		if first_line == self.success_keyword:
			return True, self.success_reason
		# 未达成时注入引导建议。
		# Inject guidance when the target condition has not been reached.
		if len(answer.split("\n")) > 1:
			guidance = "\n".join(answer.split("\n")[1:]).strip()
			if guidance:
				ctx.add_system_message(f"[引导] {guidance}", round_num)
		return False, ""
