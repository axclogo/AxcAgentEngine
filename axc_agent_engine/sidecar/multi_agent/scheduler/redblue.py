"""RedBlueScheduler — 红蓝对抗调度。
RedBlueScheduler — red-blue adversarial scheduling.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class RedBlueScheduler:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
红方和蓝方交替发言，每 N 轮裁判发言。
	Alternates red and blue speakers, with a judge turn every N rounds.
	"""

	def __init__(self, agents: list, persona: dict[str, dict] | None = None,
				 judge_interval: int = 3) -> None:
		persona = persona or {}
		self._red = []
		self._blue = []
		self._judge = None
		has_team_tag = False
		for agent in agents:
			team = persona.get(agent.name, {}).get("team", "")
			if team == "judge":
				self._judge = agent
				has_team_tag = True
			elif team == "blue":
				self._blue.append(agent)
				has_team_tag = True
			elif team == "red":
				self._red.append(agent)
				has_team_tag = True
		if not has_team_tag:
			#English: Bilingual note. 中文：没有 team 标记时，前半红后半蓝。
			#English: Without team tags, split the list into red first half and blue second half. 中文：源码说明。
			mid = len(agents) // 2
			self._red = agents[:mid]
			self._blue = agents[mid:]
		self._judge_interval = judge_interval

	def steps_per_round(self, agents: list) -> int:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
红 + 蓝 = 2 步 = 1 轮。
		Red plus blue equals two steps per round.
		"""
		return 2

	def select_speakers(self, ctx: SharedContext, agents: list,
						step: int) -> list:
		#English: Source note. 中文：裁判轮。
		#English: Judge turn. 中文：源码说明。
		if self._judge and step > 0 and step % self._judge_interval == 0:
			return [self._judge]
		#English: Source note. 中文：红蓝交替。
		#English: Alternate red and blue. 中文：源码说明。
		if step % 2 == 0:
			team = self._red
		else:
			team = self._blue
		if not team:
			return agents[:1] if agents else []
		idx = (step // 2) % len(team)
		return [team[idx]]
