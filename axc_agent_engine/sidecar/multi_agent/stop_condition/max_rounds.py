"""MaxRoundsStop — 轮次上限终止。
MaxRoundsStop — stop at the round limit.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class MaxRoundsStop:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
达到最大轮次时停止。
	Stop when the maximum round count is reached.
	"""
	def __init__(self, max_rounds: int = 10) -> None:
		self._max_rounds = max_rounds

	async def should_stop(self, ctx: SharedContext, round_num: int) -> tuple[bool, str]:
		if round_num >= self._max_rounds:
			return True, f"已达到最大轮次 {self._max_rounds}"
		return False, ""
