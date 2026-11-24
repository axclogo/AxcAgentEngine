"""SupervisorScheduler — 管理者调度。
SupervisorScheduler — supervisor-driven scheduling.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.agent import Agent
	from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext


class SupervisorScheduler:
	"""The supervisor decides which worker speaks.
	管理者决定谁发言。"""

	def __init__(self, supervisor: Agent, workers: list[Agent]) -> None:
		if supervisor is None:
			raise ValueError("supervisor 模式必须指定 supervisor Agent")
		self._supervisor = supervisor
		self._workers = workers
		self._worker_names = {w.name for w in workers}
		self._worker_index = 0

	def steps_per_round(self, agents: list) -> int:
		"""supervisor + 1 worker = 2 步 = 1 轮。
		Supervisor plus one worker equals two steps per round.
		"""
		return 2

	def select_speakers(self, ctx: SharedContext, agents: list, step: int) -> list:
		# Even-numbered steps (0, 2, 4...) are supervisor turns. 偶数步由调度者执行。
		if step % 2 == 0:
			return [self._supervisor]
		# Odd-numbered steps (1, 3, 5...) parse the supervisor reply to find a worker. 在奇数步解析调度者回复，选择工作 Agent。
		last_msg = ctx.get_last_message(exclude_agent="")
		if last_msg and last_msg["agent"] == self._supervisor.name:
			worker = self._parse_worker(last_msg["content"])
			if worker:
				return [worker]
		# If parsing fails, choose workers by fallback rotation. 解析失败时按轮询顺序选择工作 Agent。
		return [self._fallback_worker()]

	def _parse_worker(self, content: str) -> Agent | None:
		"""Parse a worker name from the supervisor reply.
		从 supervisor 回复中解析 Worker 名称。"""
		# Try the ASSIGN:name:task format. 尝试解析 ASSIGN:name:task 格式。
		match = re.search(r"ASSIGN:(\S+?):", content)
		if match:
			name = match.group(1)
			return self._find_worker(name)
		# Try matching any known worker name. 尝试匹配已知的工作 Agent 名称。
		for worker in self._workers:
			if worker.name in content:
				return worker
		return None

	def _find_worker(self, name: str) -> Agent | None:
		for worker in self._workers:
			if worker.name == name:
				return worker
		return None

	def _fallback_worker(self) -> Agent:
		"""Fallback: rotate through workers.
		降级：轮流选 Worker。"""
		worker = self._workers[self._worker_index % len(self._workers)]
		self._worker_index += 1
		return worker
