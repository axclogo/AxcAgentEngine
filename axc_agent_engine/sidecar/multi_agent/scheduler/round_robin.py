"""RoundRobinScheduler — 轮流发言。
RoundRobinScheduler — stable round-robin speaking order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class RoundRobinScheduler:
	"""Selects one speaker at a time in stable order.
	按顺序轮流，每次选一个。"""

	def steps_per_round(self, agents: list) -> int:
		"""Every agent gets one turn per round in round-robin modes. 轮询模式下每个 Agent 每轮发言一次。"""
		return len(agents)

	def select_speakers(self, ctx: SharedContext, agents: list, step: int) -> list:
		if not agents:
			return []
		return [agents[step % len(agents)]]
