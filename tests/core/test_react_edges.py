import asyncio
from unittest.mock import AsyncMock

import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.execution_run import StreamLLMBridge
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.react_loop import ReActTurnResult, ReActTurnRunner, ToolCallFlow, por_visible_event
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.strategies.react import ReActLoop
from axc_agent_engine.plugins.strategies.react_kernel import ReActKernel
from axc_agent_engine.plugins.strategy import StrategyRuntime
from axc_agent_engine.runtime.checkpoint import CheckpointStatus
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


class _Provider:
	model = "unit"
	tool_name_mapping = None

	def __init__(self, responses=None):
		self.responses = list(responses or [])

	async def chat(self, messages, tools=None, **kwargs):
		return self.responses.pop(0)

	async def stream(self, messages, tools=None, **kwargs):
		raise AssertionError("stream not expected")


class _StopPlugin(BasePlugin):
	name = "stop"

	def __init__(self):
		self.stop = False

	def should_stop(self, ctx):
		return self.stop, "halted"

	async def on_round_end(self, ctx, user_message, assistant_content, tool_calls):
		self.stop = True

	async def on_error(self, ctx, error):
		ctx.runtime.plugin_states["error_seen"] = str(error)


class _FailingTurnRunner:
	async def run(self, *args, **kwargs):
		raise RuntimeError("turn down")
		if False:
			yield None


class _ToolThenDoneTurnRunner:
	def __init__(self):
		self.calls = 0

	async def run(self, *args, **kwargs):
		self.calls += 1
		if self.calls == 1:
			yield ReActTurnResult(
				message={"content": "", "tool_calls": [{"id": "tc"}]},
				content="",
				parsed_calls=[{"name": "t", "id": "tc", "arguments": {}}],
				has_tool_calls=True,
			)
		else:
			yield ReActTurnResult(message={"content": "done"}, content="done")


def _kernel(ctx=None, messages=None, plugin_manager=None):
	provider = _Provider([LLMResponse(message=LLMMessage(content="ok"))])
	return ReActKernel(
		LLMCaller(provider, None, PluginManager([])),
		ToolRegistry(),
		plugin_manager or PluginManager([]),
		ctx or ExecutionContext(config=ExecutionConfig(max_rounds=2)),
		messages or MessageStore(),
	)


def official_loop(ctx=None, plugins=None, handoff=None, checkpoint=None):
	ctx = ctx or ExecutionContext(config=ExecutionConfig(max_rounds=2))
	pm = plugins or PluginManager([])
	llm = LLMCaller(_Provider([LLMResponse(message=LLMMessage(content="ok"))]), None, pm)
	runtime = StrategyRuntime(
		llm,
		ToolRegistry(),
		pm,
		ctx,
		MessageStore(),
		StreamLLMBridge(llm, ctx),
		checkpoint or AsyncMock(),
		asyncio.get_running_loop().time(),
	)
	return ReActLoop(runtime, handoff)


@pytest.mark.asyncio
async def test_react_kernel_sets_start_time_and_reports_timeout():
	ctx = ExecutionContext(config=ExecutionConfig(total_timeout=0.5, max_rounds=2))
	kernel = official_loop(ctx)
	kernel.runtime._start_time -= 1

	events = [event async for event in kernel.run("hi", None)]

	assert events[0].type == EventType.ERROR
	assert "总执行超时" in events[0].content


@pytest.mark.asyncio
async def test_react_kernel_reports_turn_error():
	ctx = ExecutionContext(config=ExecutionConfig(max_rounds=1))
	plugin = _StopPlugin()
	kernel = official_loop(ctx, PluginManager([plugin]))
	kernel.runtime.model_turn = _FailingTurnRunner().run
	events = [event async for event in kernel.run("hi", None)]
	assert events[0].content == "turn down"
	assert ctx.runtime.plugin_states["error_seen"] == "turn down"


@pytest.mark.asyncio
async def test_react_kernel_delegates_to_supplied_handoff():
	async def replacement(message, user_message):
		yield Event.done(f"{message['content']}:{user_message}")

	kernel = official_loop(handoff=replacement)
	events = [event async for event in kernel.run("hi", None)]
	assert events[-1].content == "ok:hi"


def _kernel_with_runner(runner, ctx=None):
	kernel = _kernel(ctx=ctx or ExecutionContext(config=ExecutionConfig(max_rounds=2)))
	kernel._turn_runner = runner
	return kernel


@pytest.mark.asyncio
async def test_react_kernel_run_step_timeout_and_round_limit():
	kernel = _kernel_with_runner(_ToolThenDoneTurnRunner(), ExecutionContext(config=ExecutionConfig(max_rounds=3)))
	results = [item async for item in kernel.run_step(max_rounds=1)]
	assert results[-1].content == "步骤超过子循环轮次限制"

	closed = asyncio.Event()

	class BlockedTurn:
		async def run(self, **kwargs):
			try:
				await asyncio.Event().wait()
				yield
			finally:
				closed.set()

	kernel = _kernel_with_runner(BlockedTurn())
	async with asyncio.timeout(2):
		results = [item async for item in kernel.run_step(max_rounds=1, step_timeout=0.02)]
	assert results[0].failed and "步骤执行超时" in results[0].content
	assert closed.is_set()


@pytest.mark.asyncio
async def test_react_turn_runner_stream_fallback_event_filter_and_tool_flow_error(monkeypatch):
	ctx = ExecutionContext(config=ExecutionConfig(stream=True))
	ctx.state.fallback_triggered = True
	ctx.state.fallback_reason = "primary failed"
	messages = MessageStore()
	runner = ReActTurnRunner(object(), ToolRegistry(), PluginManager([]), ctx, messages)

	async def stream_llm_call(messages, tools):
		yield Event(type=EventType.STREAM_DELTA, content="hidden")
		yield ({"role": "assistant", "content": "ok"}, [Event(type=EventType.COST_UPDATE, content="cost")])

	seen = []
	events = [
		item
		async for item in runner.run(
			"hi",
			stream_llm_call=stream_llm_call,
			event_filter=lambda event: event.type != EventType.STREAM_DELTA,
			event_sink=seen.append,
		)
	]
	assert [event.type for event in seen] == [EventType.STATE_CHANGE, EventType.COST_UPDATE]
	assert any(isinstance(item, ReActTurnResult) and item.content == "ok" for item in events)

	async def broken_flow(self, tool_calls, emit_events=True, event_sink=None):
		raise RuntimeError("flow down")

	monkeypatch.setattr(ToolCallFlow, "run", broken_flow)
	tool_message = {
		"role": "assistant",
		"content": "",
		"tool_calls": [
			{
				"id": "tc",
				"type": "function",
				"function": {"name": "x", "arguments": "{}"},
			}
		],
	}
	runner = ReActTurnRunner(
		LLMCaller(
			_Provider([LLMResponse(message=LLMMessage(content="", tool_calls=tool_message["tool_calls"]))]),
			None,
			PluginManager([]),
		),
		ToolRegistry(),
		PluginManager([]),
		ExecutionContext(),
		MessageStore(),
	)
	with pytest.raises(RuntimeError, match="flow down"):
		[item async for item in runner.run("hi")]


@pytest.mark.asyncio
async def test_tool_call_flow_can_suppress_events_and_por_event_filter():
	registry = ToolRegistry()
	registry.register(
		ToolDefinition(
			name="x",
			execute=lambda args, ctx: ToolOutput.text("ok"),
			is_read_only=True,
		)
	)
	flow = ToolCallFlow(registry, PluginManager([]), ExecutionContext(), MessageStore())
	result = await flow.run(
		[
			{
				"id": "tc",
				"type": "function",
				"function": {"name": "x", "arguments": "{}"},
			}
		],
		emit_events=False,
	)
	assert result.events == []
	assert result.parsed_calls[0]["name"] == "x"
	assert por_visible_event(Event(type=EventType.THINKING_START))
	assert not por_visible_event(Event(type=EventType.DONE))


@pytest.mark.asyncio
async def test_react_kernel_checkpoint_and_stop_after_tool_round():
	checkpoints = []

	async def save_checkpoint(kind, status, extra_state):
		checkpoints.append((kind, status, extra_state))

	plugin = _StopPlugin()
	kernel = official_loop(
		ExecutionContext(config=ExecutionConfig(max_rounds=3)), PluginManager([plugin]), checkpoint=save_checkpoint
	)
	kernel.runtime.model_turn = _ToolThenDoneTurnRunner().run
	events = [event async for event in kernel.run("hi", None)]

	assert events[-1].content == "halted"
	assert checkpoints[0] == ("round", CheckpointStatus.RUNNING, {"phase": "round_start"})
	assert checkpoints[-1][1] == CheckpointStatus.COMPLETED


async def test_por_siblings_charge_parent_rounds_before_model_work():
	parent = ExecutionContext(config=ExecutionConfig(max_rounds=2))
	parent.state.current_round = 1
	children = [parent.fork_for_child() for _ in range(2)]
	runners = [_ToolThenDoneTurnRunner() for _ in children]

	async def run(ctx, runner):
		kernel = _kernel_with_runner(runner, ctx)
		return [item async for item in kernel.run_step(max_rounds=2, round_budget=parent.state)]

	results = await asyncio.gather(*(run(child, runner) for child, runner in zip(children, runners, strict=True)))
	assert parent.state.current_round == 2
	assert sum(child.state.current_round for child in children) == sum(runner.calls for runner in runners) == 1
	assert all(items[-1].failed and "总轮次限制" in items[-1].content for items in results)
