from __future__ import annotations

import sys
import types

from axc_agent_engine.core.events import Event
from axc_agent_engine.runtime.checkpoint import Checkpoint, InMemoryCheckpointStore
from axc_agent_engine.workflow import (
	MemoryWorkflowRuntime,
	WorkflowResumeRequest,
	WorkflowRunRequest,
	WorkflowStatus,
	create_workflow_runtime,
)
from axc_agent_engine.workflow.state import resume_snapshot_from_checkpoint, session_id_from_checkpoint


async def test_memory_workflow_runtime_wraps_resume_handler():
	runtime = MemoryWorkflowRuntime()
	seen: list[str] = []

	async def handler(plan):
		assert plan.kind == "handler"
		seen.append("called")
		yield Event.done("resumed")

	events = []
	async for event in runtime.resume(WorkflowResumeRequest(run_id="r1", message="", handler=handler)):
		events.append(event)

	assert seen == ["called"]
	assert events[-1].content == "resumed"
	assert await runtime.status("r1") == WorkflowStatus.COMPLETED


async def test_memory_workflow_runtime_run_pause_missing_and_failed_resume():
	runtime = MemoryWorkflowRuntime()

	async def handler():
		yield Event.error("failed")

	events = [event async for event in runtime.run(WorkflowRunRequest(run_id="run", handler=handler))]
	assert events[-1].content == "failed"
	assert await runtime.status("run") == WorkflowStatus.FAILED
	await runtime.pause("run", "wait")
	assert await runtime.status("run") == WorkflowStatus.PAUSED
	assert await runtime.status("missing") == WorkflowStatus.MISSING

	store = InMemoryCheckpointStore()
	events = [event async for event in runtime.resume(WorkflowResumeRequest(run_id="none", message="", handler=lambda plan: handler(), checkpoint_store=store))]
	assert events[-1].type.value == "error"


async def test_agent_resume_stream_delegates_to_workflow_runtime():
	from axc_agent_engine.agent import Agent
	from axc_agent_engine.core.context import ExecutionServices
	from axc_agent_engine.core.schema import RuntimeConfig
	from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus, InMemoryCheckpointStore
	from tests.core.test_executor_checkpoint import _recording_provider

	class RecordingWorkflowRuntime(MemoryWorkflowRuntime):
		def __init__(self) -> None:
			super().__init__()
			self.resume_requests: list[WorkflowResumeRequest] = []

		async def resume(self, request: WorkflowResumeRequest):
			self.resume_requests.append(request)
			async for event in super().resume(request):
				yield event

	store = InMemoryCheckpointStore()
	await store.save(Checkpoint(
		run_id="delegated",
		sequence=1,
		kind="round",
		status=CheckpointStatus.INTERRUPTED,
		state={
			"current_round": 1,
			"messages": [{"role": "user", "content": "before"}],
			"metadata": {"session_id": "s", "agent_name": "agent"},
		},
	))
	captured_messages: list[list[dict]] = []
	workflow = RecordingWorkflowRuntime()
	agent = Agent(
		name="agent",
		description="",
		system_prompt="",
		runtime=RuntimeConfig(max_rounds=5),
		plugins=[],
		default_model=_recording_provider([{"content": "done"}], captured_messages),
		fallback_model=None,
		services=ExecutionServices(checkpoint_store=store),
		workflow_runtime=workflow,
	)

	events = []
	async for event in agent.resume_stream("delegated", run_options={"stream": False}):
		events.append(event)

	assert len(workflow.resume_requests) == 1
	assert workflow.resume_requests[0].run_id == "delegated"
	assert events[-1].content == "done"


def test_workflow_state_helpers_support_envelope_and_legacy():
	cp = Checkpoint(
		run_id="r",
		kind="round",
		state={
			"cursor": {"current_round": 3},
			"usage": {"input_tokens": 4, "output_tokens": 5},
			"messages": [{"role": "user", "content": "x"}],
			"metadata": {"session_id": "s"},
		},
	)
	assert session_id_from_checkpoint(cp) == "s"
	snapshot = resume_snapshot_from_checkpoint(cp)
	assert snapshot["current_round"] == 3
	assert snapshot["input_tokens"] == 4

	cp = Checkpoint(run_id="r", kind="round", state={}, metadata={"session_id": "m"})
	assert session_id_from_checkpoint(cp) == "m"
	cp = Checkpoint(run_id="r", kind="por", state={"plan": {}, "metadata": {"session_id": "p"}})
	assert "por_checkpoint" in resume_snapshot_from_checkpoint(cp)


def test_workflow_factory_prefers_burr_when_available():
	import pytest

	pytest.importorskip("burr")
	runtime = create_workflow_runtime()

	assert runtime.__class__.__name__ == "BurrWorkflowRuntime"


async def test_burr_workflow_runtime_records_resume_action():
	import pytest

	pytest.importorskip("burr")
	from axc_agent_engine.workflow.burr_runtime import BurrWorkflowRuntime

	runtime = BurrWorkflowRuntime()

	async def handler(plan):
		assert plan.kind == "handler"
		yield Event.done("done")

	events = []
	async for event in runtime.resume(WorkflowResumeRequest(run_id="burr-run", message="", handler=handler)):
		events.append(event)

	assert events[-1].content == "done"
	assert await runtime.status("burr-run") == WorkflowStatus.COMPLETED


async def test_burr_workflow_runtime_with_fake_burr(monkeypatch):
	class State:
		def __init__(self, values=None):
			self._values = dict(values or {})

		def update(self, **kwargs):
			self._values.update(kwargs)
			return self

		def get_all(self):
			return dict(self._values)

	def action(reads=None, writes=None):
		def decorator(func):
			return func
		return decorator

	class Application:
		def __init__(self, values, actions):
			self.values = values
			self.actions = actions

		def run(self, halt_after=None):
			state = State(self.values)
			for action_func in self.actions:
				state = action_func(state)
			return None, None, state

	class ApplicationBuilder:
		def __init__(self):
			self.values = {}
			self.actions = []

		def with_state(self, **values):
			self.values.update(values)
			return self

		def with_actions(self, *actions):
			self.actions.extend(actions)
			return self

		def with_entrypoint(self, name):
			self.entrypoint = name
			return self

		def build(self):
			return Application(self.values, self.actions)

	burr = types.ModuleType("burr")
	core = types.ModuleType("burr.core")
	core.State = State
	core.ApplicationBuilder = ApplicationBuilder
	core.action = action
	core.expr = lambda value: value
	monkeypatch.setitem(sys.modules, "burr", burr)
	monkeypatch.setitem(sys.modules, "burr.core", core)

	from axc_agent_engine.workflow.burr_runtime import BurrWorkflowRuntime

	runtime = BurrWorkflowRuntime()

	async def run_handler():
		yield Event.done("ran")

	run_events = [event async for event in runtime.run(WorkflowRunRequest(run_id="run", handler=run_handler, metadata={"m": 1}))]
	assert run_events[-1].content == "ran"
	assert await runtime.status("run") == WorkflowStatus.COMPLETED

	async def resume_handler(plan):
		assert plan.kind == "handler"
		assert runtime._records["resume"].state["action_name"] == "resume"
		assert runtime._records["resume"].state["run_id"] == "resume"
		yield Event.error("resume failed")

	resume_events = [
		event
		async for event in runtime.resume(WorkflowResumeRequest(
			run_id="resume",
			message="continue",
			handler=resume_handler,
			metadata={"user": "u"},
		))
	]
	assert resume_events[-1].content == "resume failed"
	assert await runtime.status("resume") == WorkflowStatus.FAILED
	await runtime.pause("resume", "manual")
	assert await runtime.status("resume") == WorkflowStatus.PAUSED


def test_burr_workflow_runtime_missing_dependency(monkeypatch):
	from axc_agent_engine.workflow.burr_runtime import BurrWorkflowRuntime

	monkeypatch.setitem(sys.modules, "burr", None)
	monkeypatch.setitem(sys.modules, "burr.core", None)

	try:
		BurrWorkflowRuntime()
	except RuntimeError as exc:
		assert "workflow" in str(exc)
