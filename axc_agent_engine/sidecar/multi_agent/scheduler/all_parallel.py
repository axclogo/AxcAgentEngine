"""AllParallelScheduler — 所有人并行发言。
AllParallelScheduler — all agents speak in parallel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class AllParallelScheduler:
	"""All agents act in parallel, mainly for Social mode.
	所有人并行行动（Social 模式用）。"""

	def select_speakers(self, ctx: SharedContext, agents: list, step: int) -> list:
		return list(agents)

	def steps_per_round(self, agents: list) -> int:
		"""Parallel mode treats one step as one round.
		并行 = 1 步 = 1 轮。"""
		return 1
