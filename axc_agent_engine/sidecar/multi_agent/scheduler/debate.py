"""DebateScheduler — 正反方交替发言。
DebateScheduler — alternates pro and con speakers.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class DebateScheduler:
	"""正方和反方交替发言。
	Alternates pro and con speakers.
	"""

	def __init__(self, agents: list, judge: object | None = None) -> None:
		"""agents 列表中，偶数索引为正方，奇数索引为反方。
		In the agents list, even indices are pro and odd indices are con.
		"""
		if len(agents) < 2:
			raise ValueError("辩论模式至少需要 2 个 Agent")
		self._pro = [agents[i] for i in range(0, len(agents), 2)]
		self._con = [agents[i] for i in range(1, len(agents), 2)]
		self._judge = judge

	def steps_per_round(self, agents: list) -> int:
		"""正方 + 反方 = 2 步 = 1 轮。
		Pro plus con equals two steps per round.
		"""
		return 2

	def select_speakers(self, ctx: SharedContext, agents: list,
						step: int) -> list:
		# 偶数步正方，奇数步反方。
		# Even steps use pro speakers; odd steps use con speakers.
		if step % 2 == 0:
			team = self._pro
		else:
			team = self._con
		idx = (step // 2) % len(team)
		return [team[idx]]
