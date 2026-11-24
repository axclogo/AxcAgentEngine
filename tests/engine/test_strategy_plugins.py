import asyncio
from dataclasses import replace

import pytest
import yaml

from axc_agent_engine import AgentModels, Engine, Event, EventType, InMemoryCheckpointStore, PluginRegistry
from axc_agent_engine.core.errors import PluginInitError
from axc_agent_engine.core.run_request import RunRequest
from axc_agent_engine.plugins.builtin import PORPlugin, ReActPlugin
from axc_agent_engine.plugins.config_schema import config_schema
from axc_agent_engine.plugins.strategy import ExecutionStrategyPlugin
from axc_agent_engine.runtime.checkpoint import CheckpointStatus
from tests.planning.test_routing_integration import PLAN_CONTENT, RoutingProvider


class CountPlugin(ExecutionStrategyPlugin):
	name = "count"
	config_schema = config_schema(name, "Count", "Count steps", [])

	def create_loop(self, runtime):
		class Counter:
			async def run(self, message, state):
				if state:
					yield Event.done(f"resumed {state['label']}")
					return
				self.label = message
				await asyncio.sleep(0)
				await runtime.save_state("counter", {"label": self.label})
				if message == "pause":
					yield Event.cancelled("paused")
					return
				yield Event.done(self.label)

		return Counter()


def template(tmp_path, plugins, strategy="", store=None, *, audit=None):
	registry = PluginRegistry()
	registry.register_many([ReActPlugin, PORPlugin, CountPlugin])
	engine = Engine(plugin_registry=registry, checkpoint_store=store, audit_sink=audit)
	path = tmp_path / "agent.yaml"
	path.write_text(yaml.safe_dump({"name": "test", "plugins": plugins, "runtime": {"strategy": strategy}}))
	return engine, engine.load_agent_template(str(path))


async def test_third_party_loop_isolated_and_resumes_in_new_agent(tmp_path, mock_llm):
	store = InMemoryCheckpointStore()
	config = {"count": {"enabled": True}}
	engine, spec = template(tmp_path, config, store=store)
	agent = spec.instantiate(models=AgentModels(default=mock_llm))
	try:
		assert await asyncio.gather(agent.chat("one", session_id="x"), agent.chat("two", session_id="y")) == [
			"one",
			"two",
		]
		events = [e async for e in agent.stream("pause", session_id="s", metadata={"run_id": "r"})]
		assert events[-1].type == EventType.CANCELLED
	finally:
		await engine.close()
	engine, spec = template(tmp_path, config, store=store)
	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm))
		assert await agent.resume("r") == "resumed pause"
		checkpoints = await store.list("r")
		assert len({cp.sequence for cp in checkpoints}) == len(checkpoints)
		assert checkpoints[-1].state["strategy"] == "count"
		mock_llm.chat.assert_not_awaited()
		other_engine, other = template(tmp_path, {"react": {"enabled": True}}, store=store)
		try:
			with pytest.raises(ValueError, match="requires strategy count"):
				await other.instantiate(models=AgentModels(default=mock_llm)).resume("r")
		finally:
			await other_engine.close()
	finally:
		await engine.close()


@pytest.mark.parametrize(
	"strategy,plugins,match",
	[
		("count", {}, "registered and enabled"),
		("", {"por": {"enabled": True}, "react": {"enabled": True}}, "Multiple"),
		("", {"por": {"enabled": False}}, "disabled"),
	],
)
def test_invalid_selection_fails_at_instantiation(tmp_path, mock_llm, strategy, plugins, match):
	_, spec = template(tmp_path, plugins, strategy)
	with pytest.raises(PluginInitError, match=match):
		spec.instantiate(models=AgentModels(default=mock_llm))


@pytest.mark.parametrize("strategy", ["react", "por"])
async def test_explicit_official_driver(tmp_path, strategy):
	config = {"react": {"enabled": True}, "por": {"enabled": True, "mode": "auto"}}
	store = InMemoryCheckpointStore()
	engine, spec = template(tmp_path, config, strategy, store)
	responses = [{"content": PLAN_CONTENT}, {"content": "step"}, {"content": "summary"}]
	provider = RoutingProvider(responses)
	try:
		agent = spec.instantiate(models=AgentModels(default=provider))
		events = [event async for event in agent.stream("hi", metadata={"run_id": "r"})]
		assert [e.content for e in events if e.type == EventType.DONE] == [
			PLAN_CONTENT if strategy == "react" else "summary"
		]
		assert len(provider.requests) == (1 if strategy == "react" else 3)
		if strategy == "por":
			finished_step = next(cp for cp in await store.list("r") if cp.state["phase"] == "goal_done")
			await store.save(replace(finished_step, run_id="replay"))
			provider.responses.append({"content": "resumed summary"})
			events = [e async for e in agent.resume_stream("replay")]
			assert events[-1].content == "resumed summary"
			assert all(e.type != EventType.STEP_START for e in events)
	finally:
		await engine.close()


async def test_official_por_implements_named_loop_arguments(tmp_path, mock_llm):
	engine, spec = template(tmp_path, {"por": {"enabled": True}})
	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm))
		ctx = agent._execution_context(RunRequest.create(user_message="test", stream=False))
		executor = agent._create_executor(ctx)
		events = [event async for event in executor._loop.run(message="test", state={})]
		assert events[-1].type == EventType.ERROR
	finally:
		await engine.close()


@pytest.mark.parametrize("hook", ["on_execution_start", "on_execution_end"])
async def test_failed_hook_releases_run_control(tmp_path, mock_llm, monkeypatch, hook):
	store = InMemoryCheckpointStore()
	engine, spec = template(tmp_path, {"count": {"enabled": True}}, store=store)
	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm))

		async def fail(*args):
			raise ValueError("hook failed")

		monkeypatch.setattr(CountPlugin, hook, fail)
		with pytest.raises(ValueError, match="hook failed"):
			async for event in agent.stream("message", metadata={"run_id": "failed-hook"}):
				assert event.type != EventType.DONE
		assert agent._services.run_control.token("failed-hook") is None
		assert (await store.latest("failed-hook")).status == CheckpointStatus.FAILED
	finally:
		await engine.close()


@pytest.mark.parametrize(
	"fields,match",
	[
		({"messages": []}, "reserved"),
		({"forkable": "false"}, "forkable must be a bool"),
		({"plugin_scopes": {"memory": "other"}}, "reserved"),
		({"strategy_state": ["invalid"]}, "must be a dict"),
	],
)
async def test_strategy_checkpoint_cannot_replace_engine_fields(tmp_path, mock_llm, monkeypatch, fields, match):
	class Writer:
		def __init__(self, runtime):
			self.runtime = runtime

		async def run(self, message, state):
			await self.runtime._checkpoint("step", CheckpointStatus.RUNNING, fields)
			yield Event.done("unexpected")

	monkeypatch.setattr(CountPlugin, "create_loop", lambda plugin, runtime: Writer(runtime))
	engine, spec = template(tmp_path, {"count": {"enabled": True}})
	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm))
		with pytest.raises(ValueError, match=match):
			await agent.chat("keep my history")
	finally:
		await engine.close()


@pytest.mark.parametrize("finish", [False, True])
async def test_loop_must_finish_and_closes_at_first_terminal(tmp_path, mock_llm, monkeypatch, finish):
	closed = []

	class Driver:
		async def run(self, message, state):
			try:
				yield Event.delta("working")
				if finish:
					yield Event.done("finished")
					raise AssertionError("loop advanced past DONE")
			finally:
				closed.append(message)

	monkeypatch.setattr(CountPlugin, "create_loop", lambda plugin, runtime: Driver())
	engine, spec = template(tmp_path, {"count": {"enabled": True}})
	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm))
		if finish:
			assert await agent.chat("go") == "finished"
		else:
			with pytest.raises(RuntimeError, match="without a terminal event"):
				await agent.chat("go")
		assert closed == ["go"]
	finally:
		await engine.close()


async def test_strategy_runtime_applies_tool_policy_and_preserves_terminal_metadata(tmp_path, mock_llm, monkeypatch):
	from axc_agent_engine import InMemoryAuditSink, ToolDefinition
	from axc_agent_engine.plugins.testing import assert_tool_denied

	audit = InMemoryAuditSink()

	store = InMemoryCheckpointStore()
	called = []

	class ToolLoop:
		def __init__(self, runtime):
			self.runtime = runtime

		async def run(self, message, state):
			results = await self.runtime.execute_tools([{"id": "test-call", "name": "restricted", "arguments": {}}])
			self.runtime.append_tool_results(results)
			yield Event.done(results[0].error, {"custom": "value", "usage": {"total_tokens": 111}})

	async def restricted(args, ctx):
		called.append(args)
		raise AssertionError("unauthorized tool executed")

	monkeypatch.setattr(CountPlugin, "create_loop", lambda plugin, runtime: ToolLoop(runtime))
	monkeypatch.setattr(
		CountPlugin,
		"get_tools",
		lambda plugin: [ToolDefinition(name="restricted", capability="file_read", execute=restricted)],
	)
	engine, spec = template(tmp_path, {"count": {"enabled": True}}, store=store, audit=audit)
	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm))
		events = [event async for event in agent.stream("hi", metadata={"run_id": "tool-policy"})]
		assert events[-1].type == EventType.DONE
		assert "file_read" in events[-1].content
		assert events[-1].metadata["custom"] == "value"
		replay = [e async for e in agent.resume_stream("tool-policy")]
		assert len(replay) == 1 and replay[0].metadata == events[-1].metadata
		assert events[-1].metadata["usage"]["total_tokens"] == 0
		assert not called
		assert_tool_denied(await audit.list_events(), "test-call")
		assert agent.inspect()["tools"][0]["capability_allowed"] is False
		assert (await store.latest("tool-policy")).state["messages"][-1]["tool_call_id"] == "test-call"
	finally:
		await engine.close()


async def test_external_contract_checks_and_early_stream_close(tmp_path, mock_llm, monkeypatch):
	from axc_agent_engine.plugins.testing import (
		assert_cancellable_run,
		assert_isolated_runs,
		assert_stream_closes,
		assert_stream_contract,
	)

	store = InMemoryCheckpointStore()
	engine, spec = template(tmp_path, {"count": {"enabled": True}}, store=store)
	ready = asyncio.Event()
	released = asyncio.Event()

	class Driver:
		def __init__(self, runtime):
			self.runtime = runtime

		async def run(self, message, state):
			try:
				self.runtime.begin_round()
				assert self.runtime.tool_schemas() == []
				self.runtime.append_message({"role": "assistant", "content": message})
				if message == "block":
					ready.set()
					await asyncio.Event().wait()
				yield Event.delta(message)
				yield Event.done(message)
			finally:
				if message == "close":
					released.set()

	monkeypatch.setattr(CountPlugin, "create_loop", lambda plugin, runtime: Driver(runtime))
	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm))
		await assert_isolated_runs(agent, {"one": "one", "two": "two"})
		await assert_cancellable_run(engine, agent, "block", ready)
		stream = agent.stream("close", session_id="s", metadata={"run_id": "closing"})
		await assert_stream_closes(stream, released)
		assert (await store.latest("closing")).status == CheckpointStatus.INTERRUPTED
		assert not engine.cancel_run("closing")
		stream = agent.stream("persist", session_id="s")
		await anext(stream)
		assert (await anext(stream)).type == EventType.DONE
		assert (await agent.get_session("s")).messages[-1]["content"] == "persist"
		await stream.aclose()
		await assert_stream_contract(agent.stream("again", session_id="s"), content="again")
	finally:
		await engine.close()


@pytest.mark.parametrize("scenario", ["round_budget", "time_budget", "cancel"])
async def test_external_runtime_enforces_run_budgets(tmp_path, mock_llm, monkeypatch, scenario):
	from axc_agent_engine.core.errors import ExecutionTimeoutError, MaxRoundsError

	class Driver:
		def __init__(self, runtime):
			self.runtime = runtime

		async def run(self, message, state):
			if scenario == "cancel":
				self.runtime._ctx.cancel("cancelled by host")
			elif scenario == "time_budget":
				self.runtime._start_time -= self.runtime._ctx.config.total_timeout + 1
			self.runtime.begin_round()
			self.runtime.begin_round()
			yield Event.done("unexpected")

	monkeypatch.setattr(CountPlugin, "create_loop", lambda plugin, runtime: Driver(runtime))
	engine, spec = template(tmp_path, {"count": {"enabled": True}})
	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm), overrides={"runtime.max_rounds": 1})
		if scenario == "cancel":
			from axc_agent_engine.plugins.testing import assert_stream_contract

			await assert_stream_contract(agent.stream("hi"), EventType.CANCELLED, "cancelled by host")
		else:
			with pytest.raises(MaxRoundsError if scenario == "round_budget" else ExecutionTimeoutError):
				await agent.chat("hi")
	finally:
		await engine.close()


@pytest.mark.parametrize(
	"items,expected,error",
	[
		([], None, AssertionError),
		([Event.done("a"), Event.delta("late")], None, AssertionError),
		([Event.done("a")], "different", AssertionError),
		(["wrong"], None, TypeError),
	],
)
async def test_contract_detects_invalid_streams(items, expected, error):
	from axc_agent_engine.plugins.testing import assert_stream_contract

	async def invalid():
		for item in items:
			yield item

	with pytest.raises(error):
		await assert_stream_contract(invalid(), content=expected)


async def test_resume_requires_checkpoint_before_model_call(tmp_path, mock_llm):
	engine, spec = template(tmp_path, {"react": {"enabled": True}})
	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm))
		events = [event async for event in agent.resume_stream("missing")]
		assert events[-1].type == EventType.ERROR
		mock_llm.chat.assert_not_awaited()
	finally:
		await engine.close()


@pytest.mark.parametrize("strategy", ["react", "por"])
async def test_closing_official_stream_releases_active_model_call(tmp_path, strategy):
	from axc_agent_engine import LLMStreamChunk
	from axc_agent_engine.plugins.testing import assert_stream_closes

	released = asyncio.Event()

	class BlockingProvider(RoutingProvider):
		async def stream(self, messages, tools=None, **kwargs):
			try:
				yield LLMStreamChunk(content_delta="partial")
				await asyncio.Event().wait()
			finally:
				released.set()

	settings = {"enabled": True}
	if strategy == "por":
		settings["mode"] = "auto"
	engine, spec = template(tmp_path, {strategy: settings})
	try:
		agent = spec.instantiate(models=AgentModels(default=BlockingProvider([])))
		await assert_stream_closes(agent.stream("close"), released)
	finally:
		await engine.close()


async def test_resume_reports_cancellation_to_text_caller(tmp_path, mock_llm, monkeypatch):
	from axc_agent_engine.core.errors import CancelledError

	store = InMemoryCheckpointStore()
	engine, spec = template(tmp_path, {"count": {"enabled": True}}, store=store)

	class CancelOnResume:
		async def run(self, message, state):
			assert state == {"label": "pause"}
			yield Event.cancelled("cancelled during resume")

	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm))
		with pytest.raises(CancelledError, match="paused"):
			await agent.chat("pause", metadata={"run_id": "r"})
		monkeypatch.setattr(CountPlugin, "create_loop", lambda *args: CancelOnResume())
		with pytest.raises(CancelledError, match="cancelled during resume"):
			await agent.resume("r")
		assert (await store.latest("r")).status == CheckpointStatus.INTERRUPTED
	finally:
		await engine.close()


async def test_inspection_of_deferred_tool_never_executes_custom_policy(tmp_path, mock_llm, monkeypatch):
	from axc_agent_engine import ToolDefinition

	class Policy:
		def evaluate(self, request):
			raise AssertionError("Inspection should not evaluate a fabricated request")

	registry = PluginRegistry()
	registry.register(CountPlugin)
	engine = Engine(plugin_registry=registry, policy_evaluator=Policy())
	path = tmp_path / "agent.yaml"
	path.write_text("name: inspector\nplugins: {count: {enabled: true}}")
	tool = ToolDefinition(name="fetch", capability="network", deferred=True)
	monkeypatch.setattr(CountPlugin, "get_tools", lambda _: [tool])
	try:
		agent = engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=mock_llm))
		description = agent.inspect()
		assert description["policy"] == "Policy"
		assert description["tools"] == [
			{
				"name": "fetch",
				"capability": "network",
				"deferred": True,
				"risk_level": tool.risk_level,
				"capability_allowed": None,
			}
		]
	finally:
		await engine.close()


async def test_total_timeout_closes_blocked_custom_loop(tmp_path, mock_llm, monkeypatch):
	closed = asyncio.Event()

	class BlockedLoop:
		async def run(self, message, state):
			try:
				yield Event.delta("started")
				await asyncio.Event().wait()
			finally:
				closed.set()

	monkeypatch.setattr(CountPlugin, "create_loop", lambda *_: BlockedLoop())
	store = InMemoryCheckpointStore()
	engine, spec = template(tmp_path, {"count": {"enabled": True}}, store=store)
	try:
		agent = spec.instantiate(models=AgentModels(default=mock_llm), overrides={"runtime.total_timeout": 1})
		async with asyncio.timeout(4):
			events = [e async for e in agent.stream("wait", metadata={"run_id": "timeout"})]
		assert [e.type for e in events] == [EventType.STREAM_DELTA, EventType.ERROR]
		assert "总执行超时" in events[-1].content and closed.is_set()
		assert (await store.latest("timeout")).status == CheckpointStatus.FAILED
	finally:
		await engine.close()


@pytest.mark.parametrize("parallel,limit,peak_expected", [(False, 4, 1), (True, 2, 2)])
async def test_custom_loop_respects_host_tool_limits(tmp_path, mock_llm, monkeypatch, parallel, limit, peak_expected):
	from axc_agent_engine import ToolDefinition
	from axc_agent_engine.tools.tool_output import ToolOutput

	active = peak = 0

	async def read(args, ctx):
		nonlocal active, peak
		active += 1
		peak = max(peak, active)
		try:
			await asyncio.sleep(0)
			return ToolOutput.text("ok")
		finally:
			active -= 1

	class Loop:
		def __init__(self, runtime):
			self.runtime = runtime

		async def run(self, message, state):
			calls = [{"name": "read", "id": str(i), "arguments": {}} for i in range(6)]
			results = await self.runtime.execute_tools(calls)
			assert [r.tool_call_id for r in results if r.success] == [c["id"] for c in calls]
			yield Event.done("done")

	monkeypatch.setattr(CountPlugin, "create_loop", lambda _, runtime: Loop(runtime))
	monkeypatch.setattr(
		CountPlugin, "get_tools", lambda _: [ToolDefinition(name="read", is_read_only=True, execute=read)]
	)
	engine, spec = template(tmp_path, {"count": {"enabled": True}})
	try:
		agent = spec.instantiate(
			models=AgentModels(default=mock_llm),
			overrides={"runtime.parallel_tool_calls": parallel, "runtime.max_parallel_tool_calls": limit},
		)
		assert await agent.chat("tools") == "done"
		assert peak == peak_expected and active == 0
	finally:
		await engine.close()
