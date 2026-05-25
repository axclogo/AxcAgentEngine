"""RoundRobinScheduler — 轮流发言。
RoundRobinScheduler — stable round-robin speaking order.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class RoundRobinScheduler:
	"""按顺序轮流，每次选一个。
	Selects one speaker at a time in stable order.
	"""
	def select_speakers(self, ctx: SharedContext, agents: list,
						step: int) -> list:
		if not agents:
			return []
		return [agents[step % len(agents)]]
