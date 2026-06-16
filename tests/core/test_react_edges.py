import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.react_kernel import ReActKernel
from axc_agent_engine.core.react_loop import ReActTurnResult, ReActTurnRunner, ToolCallFlow, por_visible_event
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
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


class _NoResultTurnRunner:
	async def run(self, *args, **kwargs):
		if False:
			yield None


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


def _kernel(ctx=None, messages=None, plugin_manager=None, enter_por=None, detect_plan=None):
	provider = _Provider([LLMResponse(message=LLMMessage(content="ok"))])
	return ReActKernel(
		LLMCaller(provider, None, PluginManager([])),
		ToolRegistry(),
		plugin_manager or PluginManager([]),
		ctx or ExecutionContext(config=ExecutionConfig(max_rounds=2)),
		messages or MessageStore(),
		enter_por=enter_por,
		detect_plan=detect_plan,
	)


@pytest.mark.asyncio
async def test_react_kernel_sets_start_time_and_reports_timeout(monkeypatch):
	ctx = ExecutionContext(config=ExecutionConfig(total_timeout=0.5, max_rounds=2))
	kernel = _kernel(ctx=ctx)
	kernel.start_time = 100.0
	monkeypatch.setattr("axc_agent_engine.core.react_kernel.time.time", lambda: 101.0)

	events = [event async for event in kernel.run("hi", lambda messages, tools: None)]

	assert events[0].type == EventType.ERROR
	assert "总执行超时" in events[0].content


@pytest.mark.asyncio
async def test_react_kernel_no_result_and_turn_error_paths():
	ctx = ExecutionContext(config=ExecutionConfig(max_rounds=1))
	plugin = _StopPlugin()
	kernel = _kernel(ctx=ctx, plugin_manager=PluginManager([plugin]))
	kernel._turn_runner = _NoResultTurnRunner()
	events = [event async for event in kernel.run("hi", lambda messages, tools: None)]
	assert events[0].content == "LLM call returned no result"

	ctx = ExecutionContext(config=ExecutionConfig(max_rounds=1))
	kernel = _kernel(ctx=ctx, plugin_manager=PluginManager([plugin]))
	kernel._turn_runner = _FailingTurnRunner()
	events = [event async for event in kernel.run("hi", lambda messages, tools: None)]
	assert events[0].content == "turn down"
	assert ctx.runtime.plugin_states["error_seen"] == "turn down"


@pytest.mark.asyncio
async def test_react_kernel_detect_plan_error_and_por_handoff():
	async def detect_error(message, user_message):
		return False, None, "bad plan"

	ctx = ExecutionContext(config=ExecutionConfig(max_rounds=1))
	kernel = _kernel(ctx=ctx, enter_por=lambda plan, user_message: None, detect_plan=detect_error)
	events = [event async for event in kernel.run("hi", lambda messages, tools: None)]
	assert events[0].content == "bad plan"

	async def detect_ok(message, user_message):
		return True, {"goal": "g"}, ""

	async def enter_por(plan, user_message):
		yield Event.done(f"por:{plan['goal']}:{user_message}")

	kernel = ReActKernel(
		LLMCaller(_Provider([LLMResponse(message=LLMMessage(content="plan"))]), None, PluginManager([])),
		ToolRegistry(),
		PluginManager([]),
		ExecutionContext(config=ExecutionConfig(max_rounds=1)),
		MessageStore(),
		enter_por=enter_por,
		detect_plan=detect_ok,
	)
	events = [event async for event in kernel.run("hi", lambda messages, tools: None)]
	assert events[0].content == "por:g:hi"


def _kernel_with_runner(runner, ctx=None):
	kernel = _kernel(ctx=ctx or ExecutionContext(config=ExecutionConfig(max_rounds=2)))
	kernel._turn_runner = runner
	return kernel


@pytest.mark.asyncio
async def test_react_kernel_run_step_timeout_no_result_and_round_limit(monkeypatch):
	ctx = ExecutionContext(config=ExecutionConfig(max_rounds=3))
	kernel = _kernel_with_runner(_NoResultTurnRunner(), ctx)
	results = [item async for item in kernel.run_step(max_rounds=1)]
	assert results[0].failed is True
	assert "没有结果" in results[0].content

	kernel = _kernel_with_runner(_ToolThenDoneTurnRunner(), ExecutionContext(config=ExecutionConfig(max_rounds=3)))
	results = [item async for item in kernel.run_step(max_rounds=1)]
	assert results[-1].content == "步骤超过子循环轮次限制"

	times = [100.0, 102.0]
	monkeypatch.setattr("axc_agent_engine.core.react_kernel.time.time", lambda: times.pop(0))
	kernel = _kernel_with_runner(_ToolThenDoneTurnRunner(), ExecutionContext(config=ExecutionConfig(max_rounds=3)))
	results = [item async for item in kernel.run_step(max_rounds=1, step_timeout=1)]
	assert results[0].content == "步骤执行超时（1s）"


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
		item async for item in runner.run(
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
		"tool_calls": [{
			"id": "tc",
			"type": "function",
			"function": {"name": "x", "arguments": "{}"},
		}],
	}
	runner = ReActTurnRunner(
		LLMCaller(_Provider([LLMResponse(message=LLMMessage(content="", tool_calls=tool_message["tool_calls"]))]), None, PluginManager([])),
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
	registry.register(ToolDefinition(
		name="x",
		execute=lambda args, ctx: ToolOutput.text("ok"),
		is_read_only=True,
	))
	flow = ToolCallFlow(registry, PluginManager([]), ExecutionContext(), MessageStore())
	result = await flow.run([{
		"id": "tc",
		"type": "function",
		"function": {"name": "x", "arguments": "{}"},
	}], emit_events=False)
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
	kernel = ReActKernel(
		LLMCaller(_Provider([]), None, PluginManager([])),
		ToolRegistry(),
		PluginManager([plugin]),
		ExecutionContext(config=ExecutionConfig(max_rounds=3)),
		MessageStore(),
		save_checkpoint=save_checkpoint,
	)
	kernel._turn_runner = _ToolThenDoneTurnRunner()

	events = [event async for event in kernel.run("hi", lambda messages, tools: None)]

	assert events[-1].content == "halted"
	assert checkpoints[0] == ("round", CheckpointStatus.RUNNING, {"phase": "round_start"})
	assert checkpoints[-1][1] == CheckpointStatus.COMPLETED
