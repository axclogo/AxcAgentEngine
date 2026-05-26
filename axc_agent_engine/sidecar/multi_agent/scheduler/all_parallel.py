"""AllParallelScheduler — 所有人并行发言。
AllParallelScheduler — all agents speak in parallel.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class AllParallelScheduler:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
所有人并行行动（Social 模式用）。
	All agents act in parallel, mainly for Social mode.
	"""
	def select_speakers(self, ctx: SharedContext, agents: list,
						step: int) -> list:
		return list(agents)

	def steps_per_round(self, agents: list) -> int:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
并行 = 1 步 = 1 轮。
		Parallel mode treats one step as one round.
		"""
		return 1
