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
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
管理者决定谁发言。
	The supervisor decides which worker speaks.
	"""

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

	def select_speakers(self, ctx: SharedContext, agents: list,
						step: int) -> list:
		#English: Bilingual note. 中文：奇数轮（0, 2, 4...）：supervisor 发言。
		#English: Even-numbered steps (0, 2, 4...) are supervisor turns. 中文：源码说明。
		if step % 2 == 0:
			return [self._supervisor]
		#English: Bilingual note. 中文：偶数轮（1, 3, 5...）：解析 supervisor 上一轮回复，找 Worker。
		#English: Odd-numbered steps (1, 3, 5...) parse the supervisor reply to find a worker. 中文：源码说明。
		last_msg = ctx.get_last_message(exclude_agent="")
		if last_msg and last_msg["agent"] == self._supervisor.name:
			worker = self._parse_worker(last_msg["content"])
			if worker:
				return [worker]
		#English: Bilingual note. 中文：解析失败，轮流选 Worker。
		#English: If parsing fails, choose workers by fallback rotation. 中文：源码说明。
		return [self._fallback_worker()]

	def _parse_worker(self, content: str) -> Agent | None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
从 supervisor 回复中解析 Worker 名称。
		Parse a worker name from the supervisor reply.
		"""
		#English: Bilingual note. 中文：尝试 ASSIGN:name:task 格式。
		#English: Try the ASSIGN:name:task format. 中文：源码说明。
		match = re.search(r'ASSIGN:(\S+?):', content)
		if match:
			name = match.group(1)
			return self._find_worker(name)
		#English: Bilingual note. 中文：尝试匹配任何已知 Worker 名称。
		#English: Try matching any known worker name. 中文：源码说明。
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
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
降级：轮流选 Worker。
		Fallback: rotate through workers.
		"""
		worker = self._workers[self._worker_index % len(self._workers)]
		self._worker_index += 1
		return worker
