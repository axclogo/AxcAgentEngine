"""旁路编排任务服务。
Sidecar orchestration task service.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from axc_agent_engine.sidecar.multi_agent import MultiAgentEvent, MultiAgentSession
from axc_agent_engine.sidecar.multi_agent.types import MultiAgentEventType, SessionMode


class OrchestrationTaskStatus(StrEnum):
	PENDING = "pending"
	RUNNING = "running"
	COMPLETED = "completed"
	FAILED = "failed"
	CANCELLED = "cancelled"


@dataclass
class OrchestrationTask:
	"""宿主可见的一次旁路编排运行记录。
	Host-visible record for one sidecar orchestration run.
	"""
	task_id: str
	mode: str
	topic: str
	agent_names: list[str]
	status: str = OrchestrationTaskStatus.PENDING
	events: list[dict[str, Any]] = field(default_factory=list)
	result: dict[str, Any] = field(default_factory=dict)
	error: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)


class OrchestrationTaskRepository:
	"""带异步变更边界的内存任务仓储。
	In-memory task repository with async mutation boundary.
	"""

	def __init__(self) -> None:
		self._tasks: dict[str, OrchestrationTask] = {}
		self._lock = asyncio.Lock()

	async def add(self, task: OrchestrationTask) -> None:
		async with self._lock:
			self._tasks[task.task_id] = task

	def get(self, task_id: str) -> OrchestrationTask | None:
		return self._tasks.get(task_id)

	def list(self) -> list[OrchestrationTask]:
		return list(self._tasks.values())


class OrchestrationEventPresenter:
	"""把会话事件和最终任务结果转换为宿主可见 payload。
	Converts session events and final task result into host-visible payloads.
	"""

	def event_to_dict(self, event: MultiAgentEvent) -> dict[str, Any]:
		return {
			"type": event.type.value if hasattr(event.type, "value") else str(event.type),
			"agent": event.agent_name,
			"content": event.content,
			"round": event.round_num,
			"metadata": dict(event.metadata),
		}

	def result(self, task: OrchestrationTask, done_reason: str) -> dict[str, Any]:
		return {
			"mode": task.mode,
			"topic": task.topic,
			"done_reason": done_reason,
			"messages": [item for item in task.events if item["type"] == MultiAgentEventType.MESSAGE.value],
		}


class OrchestrationWorker:
	"""通过 MultiAgentSession 运行一个编排任务。
	Runs one orchestration task through MultiAgentSession.
	"""

	def __init__(self, agent_getter: Any, agent_lister: Any, dispatcher: Any, utility_llm: Any = None) -> None:
		self._agent_getter = agent_getter
		self._agent_lister = agent_lister
		self._dispatcher = dispatcher
		self._utility_llm = utility_llm
		self._presenter = OrchestrationEventPresenter()

	async def run(self, task: OrchestrationTask, max_rounds: int, supervisor_name: str, persona: dict[str, dict]) -> None:
		task.status = OrchestrationTaskStatus.RUNNING
		try:
			agent_map = {agent.name: agent for agent in self._agent_lister()}
			agents = []
			for name in task.agent_names:
				agent = agent_map.get(name) or self._agent_getter(name)
				if not agent:
					raise ValueError(f"Agent '{name}' not found")
				agents.append(agent)
			try:
				session_mode = SessionMode(task.mode)
			except ValueError as exc:
				raise ValueError(f"Unknown orchestration mode: {task.mode}") from exc
			supervisor = (agent_map.get(supervisor_name) or self._agent_getter(supervisor_name)) if supervisor_name else None
			session = MultiAgentSession(
				agents,
				self._dispatcher,
				mode=session_mode,
				topic=task.topic,
				max_rounds=max(1, min(int(max_rounds), 50)),
				supervisor=supervisor,
				persona=persona,
				utility_llm=self._utility_llm,
			)
			done_reason = ""
			async for event in session.stream():
				task.events.append(self._presenter.event_to_dict(event))
				if event.type == MultiAgentEventType.DONE:
					done_reason = event.content
			task.status = OrchestrationTaskStatus.COMPLETED
			task.result = self._presenter.result(task, done_reason)
		except asyncio.CancelledError:
			task.status = OrchestrationTaskStatus.CANCELLED
			raise
		except Exception as exc:
			task.status = OrchestrationTaskStatus.FAILED
			task.error = str(exc)


class OrchestrationTaskService:
	"""在 Agent 执行链路外创建并跟踪多 Agent 编排任务。
	Create and track multi-agent orchestration tasks outside Agent execution.
	"""

	def __init__(self, agent_getter: Any, agent_lister: Any, dispatcher: Any, utility_llm: Any = None) -> None:
		self._agent_getter = agent_getter
		self._agent_lister = agent_lister
		self._dispatcher = dispatcher
		self._utility_llm = utility_llm
		self._repository = OrchestrationTaskRepository()
		self._workers: dict[str, asyncio.Task] = {}
		self._runner = OrchestrationWorker(agent_getter, agent_lister, dispatcher, utility_llm)

	async def create_task(
		self,
		agent_names: list[str],
		mode: str,
		topic: str,
		max_rounds: int = 10,
		supervisor: str = "",
		persona: dict[str, dict] | None = None,
		metadata: dict[str, Any] | None = None,
		start: bool = True,
	) -> OrchestrationTask:
		if not self._dispatcher:
			raise ValueError("MessageBus dispatcher is required for orchestration")
		if not agent_names:
			raise ValueError("agent_names is required")
		if not topic:
			raise ValueError("topic is required")
		task_id = uuid.uuid4().hex[:12]
		task = OrchestrationTask(
			task_id=task_id,
			mode=mode,
			topic=topic,
			agent_names=list(agent_names),
			metadata=dict(metadata or {}),
		)
		await self._repository.add(task)
		if start:
			self._workers[task_id] = asyncio.create_task(self._run_task(task, max_rounds, supervisor, persona or {}))
		return task

	async def run_task(
		self,
		agent_names: list[str],
		mode: str,
		topic: str,
		max_rounds: int = 10,
		supervisor: str = "",
		persona: dict[str, dict] | None = None,
		metadata: dict[str, Any] | None = None,
	) -> OrchestrationTask:
		task = await self.create_task(
			agent_names=agent_names,
			mode=mode,
			topic=topic,
			max_rounds=max_rounds,
			supervisor=supervisor,
			persona=persona,
			metadata=metadata,
			start=True,
		)
		worker = self._workers.get(task.task_id)
		if worker:
			await worker
		return task

	async def get_task(self, task_id: str) -> OrchestrationTask | None:
		return self._repository.get(task_id)

	async def list_tasks(self) -> list[OrchestrationTask]:
		return self._repository.list()

	async def cancel_task(self, task_id: str) -> bool:
		task = self._repository.get(task_id)
		if not task:
			return False
		if task.status in (OrchestrationTaskStatus.COMPLETED, OrchestrationTaskStatus.FAILED):
			return False
		worker = self._workers.get(task_id)
		if worker and not worker.done():
			worker.cancel()
			try:
				await worker
			except asyncio.CancelledError:
				pass
		task.status = OrchestrationTaskStatus.CANCELLED
		return True

	async def close(self) -> None:
		for task_id, worker in list(self._workers.items()):
			if not worker.done():
				await self.cancel_task(task_id)

	async def _run_task(self, task: OrchestrationTask, max_rounds: int, supervisor_name: str, persona: dict[str, dict]) -> None:
		await self._runner.run(task, max_rounds, supervisor_name, persona)
