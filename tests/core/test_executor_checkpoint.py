"""Tests for Executor checkpoint persistence."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus, InMemoryCheckpointStore
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionServices
from axc_agent_engine.core.executor import Executor
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.events import EventType
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMUsage, ToolDefinition
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


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


def test_executor_restores_checkpoint_state():
	pm = PluginManager([])
	caller = LLMCaller(_provider([{"content": "unused"}]), None, pm)
	ctx = ExecutionContext(config=ExecutionConfig(stream=False))
	executor = Executor(caller, ToolRegistry(), pm, ctx)
	checkpoint = Checkpoint(
		run_id="resume-run",
		sequence=2,
		state={
			"current_round": 2,
			"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
			"input_tokens": 11,
			"output_tokens": 7,
		},
	)

	executor.load_resume_snapshot(checkpoint.run_id, checkpoint.state)

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
			"current_round": 2,
			"messages": messages,
			"input_tokens": 11,
			"output_tokens": 7,
			"metadata": {"session_id": "session-a", "agent_name": "agent-a"},
		},
	)
	executor.load_resume_snapshot(checkpoint.run_id, checkpoint.state)

	events = []
	async for event in executor.run_stream("new user input should not be appended during resume"):
		events.append(event)

	assert events[-1].type == EventType.DONE
	assert captured_messages[0] == messages
	assert ctx.state.current_round == 3
	assert ctx.state.metadata["session_id"] == "session-a"
	assert ctx.state.metadata["agent_name"] == "agent-a"
	checkpoints = await store.list("resume-run-stream")
	assert checkpoints[0].state["current_round"] == 2
	assert checkpoints[-1].status == CheckpointStatus.COMPLETED


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
			"current_round": 2,
			"messages": messages,
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
