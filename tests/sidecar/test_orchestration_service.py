"""Tests for sidecar orchestration service."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from axc_agent_engine.core.dispatcher import AgentEnvelope
from axc_agent_engine.sidecar import OrchestrationTaskService, OrchestrationTaskStatus


class RecordingDispatcher:
	def __init__(self) -> None:
		self.envelopes: list[AgentEnvelope] = []

	async def request(self, envelope: AgentEnvelope, timeout: float = 60.0) -> AgentEnvelope:
		self.envelopes.append(envelope)
		return AgentEnvelope(
			sender=envelope.recipient,
			recipient=envelope.sender,
			type="reply",
			content=f"{envelope.recipient} reply",
			conversation_id=envelope.conversation_id,
			trace_id=envelope.trace_id,
		)


class BlockingDispatcher:
	def __init__(self) -> None:
		self.started = asyncio.Event()

	async def request(self, envelope: AgentEnvelope, timeout: float = 60.0) -> AgentEnvelope:
		self.started.set()
		await asyncio.sleep(60)
		raise AssertionError("cancel should interrupt dispatcher request")


async def test_orchestration_task_service_runs_multi_agent_session():
	agents = [SimpleNamespace(name="alpha"), SimpleNamespace(name="beta")]
	dispatcher = RecordingDispatcher()
	service = OrchestrationTaskService(
		agent_getter=lambda name: next((agent for agent in agents if agent.name == name), None),
		agent_lister=lambda: agents,
		dispatcher=dispatcher,
	)

	task = await service.run_task(
		agent_names=["alpha", "beta"],
		mode="debate",
		topic="ship?",
		max_rounds=1,
	)

	assert task.status == OrchestrationTaskStatus.COMPLETED
	assert task.result["mode"] == "debate"
	assert len(task.result["messages"]) == 2
	assert [envelope.recipient for envelope in dispatcher.envelopes] == ["alpha", "beta"]


async def test_orchestration_task_service_runs_group_chat_round_robin():
	agents = [SimpleNamespace(name="alpha"), SimpleNamespace(name="beta")]
	dispatcher = RecordingDispatcher()
	service = OrchestrationTaskService(
		agent_getter=lambda name: next((agent for agent in agents if agent.name == name), None),
		agent_lister=lambda: agents,
		dispatcher=dispatcher,
	)

	task = await service.run_task(
		agent_names=["alpha", "beta"],
		mode="group_chat",
		topic="ship?",
		max_rounds=1,
	)

	assert task.status == OrchestrationTaskStatus.COMPLETED
	assert task.result["mode"] == "group_chat"
	assert len(task.result["messages"]) == 2
	assert [envelope.recipient for envelope in dispatcher.envelopes] == ["alpha", "beta"]


async def test_orchestration_close_does_not_cancel_completed_task():
	agents = [SimpleNamespace(name="alpha"), SimpleNamespace(name="beta")]
	service = OrchestrationTaskService(
		agent_getter=lambda name: next((agent for agent in agents if agent.name == name), None),
		agent_lister=lambda: agents,
		dispatcher=RecordingDispatcher(),
	)

	task = await service.run_task(
		agent_names=["alpha", "beta"],
		mode="debate",
		topic="ship?",
		max_rounds=1,
	)
	await service.close()

	assert task.status == OrchestrationTaskStatus.COMPLETED


async def test_orchestration_create_validates_required_boundaries():
	agents = [SimpleNamespace(name="alpha")]
	service = OrchestrationTaskService(
		agent_getter=lambda name: next((agent for agent in agents if agent.name == name), None),
		agent_lister=lambda: agents,
		dispatcher=RecordingDispatcher(),
	)

	with pytest.raises(ValueError, match="agent_names"):
		await service.create_task([], "group_chat", "topic")
	with pytest.raises(ValueError, match="topic"):
		await service.create_task(["alpha"], "group_chat", "")
	with pytest.raises(ValueError, match="dispatcher"):
		await OrchestrationTaskService(lambda name: None, lambda: [], None).create_task(["alpha"], "group_chat", "topic")


async def test_orchestration_task_records_missing_agent_failure():
	service = OrchestrationTaskService(
		agent_getter=lambda name: None,
		agent_lister=lambda: [],
		dispatcher=RecordingDispatcher(),
	)

	task = await service.run_task(["missing"], "group_chat", "topic", max_rounds=1)

	assert task.status == OrchestrationTaskStatus.FAILED
	assert "Agent 'missing' not found" in task.error


async def test_orchestration_task_records_unknown_mode_failure():
	agents = [SimpleNamespace(name="alpha")]
	service = OrchestrationTaskService(
		agent_getter=lambda name: agents[0],
		agent_lister=lambda: agents,
		dispatcher=RecordingDispatcher(),
	)

	task = await service.run_task(["alpha"], "invalid-mode", "topic", max_rounds=1)

	assert task.status == OrchestrationTaskStatus.FAILED
	assert "Unknown orchestration mode" in task.error


async def test_orchestration_cancel_running_task_marks_cancelled():
	agents = [SimpleNamespace(name="alpha")]
	dispatcher = BlockingDispatcher()
	service = OrchestrationTaskService(
		agent_getter=lambda name: agents[0],
		agent_lister=lambda: agents,
		dispatcher=dispatcher,
	)
	task = await service.create_task(["alpha"], "group_chat", "topic", max_rounds=2)
	await asyncio.wait_for(dispatcher.started.wait(), timeout=1)

	assert await service.cancel_task(task.task_id) is True
	assert await service.cancel_task(task.task_id) is True
	assert task.status == OrchestrationTaskStatus.CANCELLED


async def test_orchestration_cancel_missing_and_finished_task_returns_false():
	agents = [SimpleNamespace(name="alpha")]
	service = OrchestrationTaskService(
		agent_getter=lambda name: agents[0],
		agent_lister=lambda: agents,
		dispatcher=RecordingDispatcher(),
	)

	task = await service.run_task(["alpha"], "group_chat", "topic", max_rounds=1)

	assert await service.cancel_task("missing") is False
	assert await service.cancel_task(task.task_id) is False
	assert await service.get_task(task.task_id) is task
	assert task in await service.list_tasks()


async def test_orchestration_create_task_can_start_later_and_preserves_metadata():
	agents = [SimpleNamespace(name="alpha")]
	service = OrchestrationTaskService(
		agent_getter=lambda name: agents[0],
		agent_lister=lambda: agents,
		dispatcher=RecordingDispatcher(),
	)

	task = await service.create_task(
		["alpha"],
		"group_chat",
		"topic",
		metadata={"tenant": "t1"},
		start=False,
	)

	assert task.status == OrchestrationTaskStatus.PENDING
	assert task.metadata == {"tenant": "t1"}
