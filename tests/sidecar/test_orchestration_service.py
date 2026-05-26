"""Tests for sidecar orchestration service."""
from __future__ import annotations

from types import SimpleNamespace

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
