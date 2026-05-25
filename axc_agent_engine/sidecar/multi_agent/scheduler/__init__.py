"""Scheduler Protocol — 调度策略"""
from __future__ import annotations

from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class Scheduler(Protocol):
	"""调度器协议：选择本轮发言的 Agent 列表"""
	def select_speakers(self, ctx: SharedContext, agents: list,
						step: int) -> list:
		"""选择本轮发言的 Agent 列表。返回多个时并行发言，返回一个时串行。"""
		...

	def steps_per_round(self, agents: list) -> int:
		"""一轮需要多少步（所有人发完一次言 = 1 轮）。默认 = agent 数量。"""
		return len(agents)
