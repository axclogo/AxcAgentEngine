"""Tests for Executor checkpoint persistence."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus, InMemoryCheckpointStore
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionServices
from axc_agent_engine.core.executor import Executor
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.events import EventType
from axc_agent_engine.core.events import Event
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMUsage, ToolDefinition
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.tracing.plugin import TracingPlugin
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.tool_output import ToolOutput
from axc_agent_engine.workflow.state import resume_snapshot_from_checkpoint


def _provider(responses: list[dict]):
	provider = MagicMock()
	call_count = [0]

	async def chat(messages, tools=None, **kwargs):
		idx = min(call_count[0], len(responses) - 1)
		call_count[0] += 1
		resp = responses[idx]
		return LLMResponse(
			message=LLMMessage(
				role=resp.get("role", "assistant"),
				content=resp.get("content", "") or "",
				tool_calls=resp.get("tool_calls", []),
			),
			usage=LLMUsage(input_tokens=1, output_tokens=1),
		)

	provider.chat = chat
	provider.stream = None
	return provider


async def _collect(executor: Executor) -> list:
	events = []
	async for event in executor.run_stream("hi"):
		events.append(event)
	return events


def _recording_provider(responses: list[dict], captured_messages: list[list[dict]]):
	provider = MagicMock()
	call_count = [0]

	async def chat(messages, tools=None, **kwargs):
		captured_messages.append(list(messages))
		idx = min(call_count[0], len(responses) - 1)
		call_count[0] += 1
		resp = responses[idx]
		return LLMResponse(
			message=LLMMessage(
				role=resp.get("role", "assistant"),
				content=resp.get("content", "") or "",
				tool_calls=resp.get("tool_calls", []),
			),
			usage=LLMUsage(input_tokens=1, output_tokens=1),
		)

	provider.chat = chat
	provider.stream = None
	return provider


async def test_executor_writes_start_round_and_done_checkpoints():
	store = InMemoryCheckpointStore()
	pm = PluginManager([])
	caller = LLMCaller(_provider([{"content": "done"}]), None, pm)
	ctx = ExecutionContext(
		config=ExecutionConfig(stream=False),
		services=ExecutionServices(checkpoint_store=store),
	)
	ctx.state.metadata["run_id"] = "run-simple"
	executor = Executor(caller, ToolRegistry(), pm, ctx)

	events = await _collect(executor)

	assert events[-1].type == EventType.DONE
	checkpoints = await store.list("run-simple")
	assert [item.kind for item in checkpoints] == ["execution", "round", "execution"]
	assert checkpoints[0].status == CheckpointStatus.RUNNING
	assert checkpoints[-1].status == CheckpointStatus.COMPLETED
	assert checkpoints[-1].state["result"] == "done"
	assert checkpoints[-1].state["metadata"]["run_id"] == "run-simple"


async def test_executor_raises_when_checkpoint_store_save_fails():
	class BrokenCheckpointStore:
		async def save(self, checkpoint):
			raise RuntimeError("checkpoint write failed")

	store = BrokenCheckpointStore()
	pm = PluginManager([])
	caller = LLMCaller(_provider([{"content": "done"}]), None, pm)
	ctx = ExecutionContext(
		config=ExecutionConfig(stream=False),
		services=ExecutionServices(checkpoint_store=store),
	)
	ctx.state.metadata["run_id"] = "run-broken"
	executor = Executor(caller, ToolRegistry(), pm, ctx)

	with pytest.raises(RuntimeError, match="checkpoint write failed"):
		await _collect(executor)


async def test_executor_writes_round_completed_checkpoint_after_tool_call():
	tool_call_msg = {
		"role": "assistant",
		"content": "",
		"tool_calls": [{"id": "tc-1", "function": {"name": "echo", "arguments": json.dumps({"msg": "hi"})}}],
	}
	store = InMemoryCheckpointStore()
	pm = PluginManager([])
	caller = LLMCaller(_provider([tool_call_msg, {"content": "final"}]), None, pm)
	registry = ToolRegistry()

	async def echo(args, ctx):
		return ToolOutput.text(args["msg"])

	registry.register(ToolDefinition(
		name="echo",
		execute=echo,
		parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
	))
	ctx = ExecutionContext(
		config=ExecutionConfig(stream=False, max_rounds=5),
		services=ExecutionServices(checkpoint_store=store),
	)
	ctx.state.metadata["run_id"] = "run-tool"
	executor = Executor(caller, registry, pm, ctx)

	events = await _collect(executor)

	assert events[-1].type == EventType.DONE
	checkpoints = await store.list("run-tool")
	round_completed = [item for item in checkpoints if item.kind == "round" and item.status == CheckpointStatus.COMPLETED]
	assert len(round_completed) == 1
	assert round_completed[0].state["tool_calls"] == ["echo"]
	assert any(msg.get("role") == "tool" for msg in round_completed[0].state["messages"])


async def test_executor_writes_failed_checkpoint_on_error_event():
	store = InMemoryCheckpointStore()
	pm = PluginManager([])
	caller = LLMCaller(_provider([{"content": ""}]), None, pm)
	ctx = ExecutionContext(
		config=ExecutionConfig(stream=False, max_rounds=0),
		services=ExecutionServices(checkpoint_store=store),
	)
	ctx.state.metadata["run_id"] = "run-error"
	executor = Executor(caller, ToolRegistry(), pm, ctx)

	events = await _collect(executor)

	assert events[-1].type == EventType.ERROR
	checkpoints = await store.list("run-error")
	assert checkpoints[-1].status == CheckpointStatus.FAILED
	assert "Exceeded max rounds" in checkpoints[-1].state["error"]


async def test_error_event_marks_trace_root_failed():
	spans = []
	tracing = TracingPlugin()
	tracing.initialize({"enabled": True, "output": "callback"}, PluginContext())
	tracing.set_callback(spans.append)
	pm = PluginManager([tracing])
	caller = LLMCaller(_provider([{"content": ""}]), None, pm)
	ctx = ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=0))
	ctx.state.metadata["run_id"] = "run-error"
	executor = Executor(caller, ToolRegistry(), pm, ctx)

	events = await _collect(executor)

	root = next(span for span in spans if span["type"] == "execution")
	assert events[-1].type == EventType.ERROR
	assert root["success"] is False
	assert root["error"]["message"] == events[-1].content


def test_executor_restores_checkpoint_state():
	pm = PluginManager([])
	caller = LLMCaller(_provider([{"content": "unused"}]), None, pm)
	ctx = ExecutionContext(config=ExecutionConfig(stream=False))
	executor = Executor(caller, ToolRegistry(), pm, ctx)
	checkpoint = Checkpoint(
		run_id="resume-run",
		sequence=2,
		state={
			"cursor": {"current_round": 2},
			"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
			"usage": {"input_tokens": 11, "output_tokens": 7},
			"metadata": {},
		},
	)

	executor.load_resume_snapshot(checkpoint.run_id, resume_snapshot_from_checkpoint(checkpoint))

	assert ctx.state.metadata["run_id"] == "resume-run"
	assert ctx.state.current_round == 2
	assert ctx.state.total_input_tokens == 11
	assert ctx.state.total_output_tokens == 7
	assert executor.message_store.get_all() == checkpoint.state["messages"]


async def test_executor_continues_from_restored_checkpoint_without_reinitializing_messages():
	captured_messages: list[list[dict]] = []
	store = InMemoryCheckpointStore()
	pm = PluginManager([])
	caller = LLMCaller(_recording_provider([{"content": "resumed"}], captured_messages), None, pm)
	ctx = ExecutionContext(
		config=ExecutionConfig(stream=False, max_rounds=5),
		services=ExecutionServices(checkpoint_store=store),
	)
	executor = Executor(caller, ToolRegistry(), pm, ctx)
	messages = [
		{"role": "system", "content": "system"},
		{"role": "user", "content": "original"},
		{"role": "assistant", "content": "partial"},
	]
	checkpoint = Checkpoint(
		run_id="resume-run-stream",
		sequence=3,
		state={
			"cursor": {"current_round": 2},
			"messages": messages,
			"usage": {"input_tokens": 11, "output_tokens": 7},
			"metadata": {"session_id": "session-a", "agent_name": "agent-a"},
		},
	)
	executor.load_resume_snapshot(checkpoint.run_id, resume_snapshot_from_checkpoint(checkpoint))

	events = []
	async for event in executor.run_stream("new user input should not be appended during resume"):
		events.append(event)

	assert events[-1].type == EventType.DONE
	assert captured_messages[0] == messages
	assert ctx.state.current_round == 3
	assert ctx.state.metadata["session_id"] == "session-a"
	assert ctx.state.metadata["agent_name"] == "agent-a"
	checkpoints = await store.list("resume-run-stream")
	assert checkpoints[0].state["cursor"]["current_round"] == 2
	assert "current_round" not in checkpoints[0].state
	assert checkpoints[-1].status == CheckpointStatus.COMPLETED


def _executor_for_branch_tests(routing_mode: str = "auto", system_prompt: str = ""):
	pm = PluginManager([])
	caller = LLMCaller(_provider([{"content": "react"}]), None, pm)
	ctx = ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=5, system_prompt=system_prompt))
	return Executor(caller, ToolRegistry(), pm, ctx, routing_mode=routing_mode), ctx


async def test_executor_restores_por_checkpoint_and_runs_resume_branch(monkeypatch):
	executor, ctx = _executor_for_branch_tests()
	seen = {}

	class Runner:
		async def run_from_checkpoint_state(self, checkpoint_state, user_message, run_id):
			seen.update({"checkpoint": checkpoint_state, "user_message": user_message, "run_id": run_id})
			yield Event.done("resumed por")

	monkeypatch.setattr(executor, "_new_por_runner", lambda: Runner())
	executor.load_resume_snapshot("por-run", {"por_checkpoint": {"step": 2}})

	events = [event async for event in executor.run_stream("continue")]

	assert events[-1].type == EventType.DONE
	assert events[-1].content == "resumed por"
	assert seen == {"checkpoint": {"step": 2}, "user_message": "continue", "run_id": "por-run"}
	assert "por_resume_checkpoint" not in ctx.state.metadata


async def test_executor_por_first_generates_and_runs_plan(monkeypatch):
	executor, _ctx = _executor_for_branch_tests(routing_mode="por_first")
	plan = MagicMock()
	plan.steps = [object()]
	seen = {}

	async def generate_plan(llm, ctx, user_message):
		seen["generated_for"] = user_message
		return plan

	class Runner:
		async def run(self, received_plan, user_message):
			seen["plan"] = received_plan
			seen["run_message"] = user_message
			yield Event.done("por done")

	monkeypatch.setattr("axc_agent_engine.core.executor.PlanningService.generate_plan", generate_plan)
	monkeypatch.setattr(executor, "_new_por_runner", lambda: Runner())

	events = [event async for event in executor.run_stream("make plan")]

	assert events[-1].content == "por done"
	assert seen["generated_for"] == "make plan"
	assert seen["plan"] is plan


async def test_executor_por_first_plan_error_and_empty_plan(monkeypatch):
	executor, _ctx = _executor_for_branch_tests(routing_mode="por_first")

	async def failing_plan(llm, ctx, user_message):
		raise RuntimeError("planner down")

	monkeypatch.setattr("axc_agent_engine.core.executor.PlanningService.generate_plan", failing_plan)
	error_events = [event async for event in executor.run_stream("bad")]
	assert error_events[-1].type == EventType.ERROR
	assert error_events[-1].content == "planner down"

	empty_executor, _ = _executor_for_branch_tests(routing_mode="por_first")
	empty_plan = MagicMock()
	empty_plan.steps = []

	async def empty_generate(llm, ctx, user_message):
		return empty_plan

	monkeypatch.setattr("axc_agent_engine.core.executor.PlanningService.generate_plan", empty_generate)
	react_events = [event async for event in empty_executor.run_stream("react fallback")]
	assert react_events[-1].type == EventType.DONE
	assert react_events[-1].content == "react"


async def test_executor_detect_plan_handoff_branches(monkeypatch):
	executor, _ctx = _executor_for_branch_tests()
	plan = MagicMock()
	plan.steps = [object()]
	executor._router.route = lambda message: SimpleNamespace(action="por_plan", plan=plan)

	assert await executor._detect_plan_handoff({}, "u") == (True, plan, "")

	empty_plan = MagicMock()
	empty_plan.steps = []
	executor._router.route = lambda message: SimpleNamespace(action="por_plan", plan=empty_plan)
	assert await executor._detect_plan_handoff({}, "u") == (False, None, "PlanningService returned an empty plan")

	executor._router.route = lambda message: SimpleNamespace(action="react", plan=None)
	assert await executor._detect_plan_handoff({}, "u") == (False, None, "")

	executor._router.route = lambda message: SimpleNamespace(action="por_plan", plan=None)

	async def failing_plan(llm, ctx, user_message):
		raise RuntimeError("no plan")

	monkeypatch.setattr("axc_agent_engine.core.executor.PlanningService.generate_plan", failing_plan)
	assert await executor._detect_plan_handoff({}, "u") == (False, None, "no plan")


async def test_executor_exception_path_writes_failed_checkpoint_and_lifecycle_error(monkeypatch):
	store = InMemoryCheckpointStore()
	executor, _ctx = _executor_for_branch_tests()
	executor._ctx.services.checkpoint_store = store
	executor._ctx.state.metadata["run_id"] = "exception-run"

	async def broken_loop(user_message):
		raise RuntimeError("loop exploded")
		yield

	monkeypatch.setattr(executor, "_react_loop", broken_loop)

	with pytest.raises(RuntimeError, match="loop exploded"):
		[event async for event in executor.run_stream("boom")]

	checkpoints = await store.list("exception-run")
	assert checkpoints[-1].status == CheckpointStatus.FAILED
	assert checkpoints[-1].state["phase"] == "exception"


def test_executor_init_messages_can_skip_user_message():
	executor, ctx = _executor_for_branch_tests(system_prompt="system")
	executor.skip_user_init = True

	executor._init_messages("user")

	assert [msg["role"] for msg in executor.message_store.get_all()] == ["system"]


async def test_agent_resume_stream_restores_latest_execution_checkpoint():
	from axc_agent_engine.agent import Agent
	from axc_agent_engine.core.schema import RuntimeConfig

	captured_messages: list[list[dict]] = []
	store = InMemoryCheckpointStore()
	messages = [
		{"role": "system", "content": "system"},
		{"role": "user", "content": "original"},
		{"role": "assistant", "content": "partial"},
	]
	await store.save(Checkpoint(
		run_id="agent-resume",
		sequence=2,
		kind="round",
		status=CheckpointStatus.INTERRUPTED,
		state={
			"cursor": {"current_round": 2},
			"messages": messages,
			"usage": {"input_tokens": 0, "output_tokens": 0},
			"metadata": {"session_id": "s1", "agent_name": "agent"},
		},
	))
	agent = Agent(
		name="agent",
		description="",
		system_prompt="system",
		runtime=RuntimeConfig(max_rounds=5),
		plugins=[],
		default_model=_recording_provider([{"content": "resumed"}], captured_messages),
		fallback_model=None,
		services=ExecutionServices(checkpoint_store=store),
	)

	events = []
	async for event in agent.resume_stream("agent-resume", run_options={"stream": False}):
		events.append(event)

	assert events[-1].type == EventType.DONE
	assert events[-1].content == "resumed"
	assert captured_messages[0] == messages
	session = await agent.get_session("s1")
	assert session is not None
	assert session.messages[-1]["content"] == "resumed"
