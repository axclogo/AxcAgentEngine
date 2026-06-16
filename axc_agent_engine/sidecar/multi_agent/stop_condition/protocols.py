"""StopCondition Protocol — 终止条件。
StopCondition Protocol — stopping condition.
"""
from __future__ import annotations

from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class StopCondition(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
终止条件协议。
	Stopping condition protocol.
	"""
	async def should_stop(self, ctx: SharedContext, round_num: int) -> tuple[bool, str]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
返回 (should_stop, reason)。
		Return (should_stop, reason).
		"""
		...
