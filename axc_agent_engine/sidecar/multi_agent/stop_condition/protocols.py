"""StopCondition Protocol — 终止条件。
StopCondition Protocol — stopping condition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class StopCondition(Protocol):
	"""Stopping condition protocol.
	终止条件协议。"""

	async def should_stop(self, ctx: SharedContext, round_num: int) -> tuple[bool, str]:
		"""Return (should_stop, reason).
		返回 (should_stop, reason)。"""
		...
