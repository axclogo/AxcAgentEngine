"""RoundRobinScheduler — 轮流发言。
RoundRobinScheduler — stable round-robin speaking order.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class RoundRobinScheduler:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
按顺序轮流，每次选一个。
	Selects one speaker at a time in stable order.
	"""
	def steps_per_round(self, agents: list) -> int:
		"""Every agent gets one turn per round in round-robin modes.
中文：此文档说明相关引擎组件的行为。"""
		return len(agents)

	def select_speakers(self, ctx: SharedContext, agents: list,
						step: int) -> list:
		if not agents:
			return []
		return [agents[step % len(agents)]]
