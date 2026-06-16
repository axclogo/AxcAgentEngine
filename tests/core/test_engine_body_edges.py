import asyncio
from types import SimpleNamespace

import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext
from axc_agent_engine.core.errors import ExecutionTimeoutError, ProviderError, SchemaError
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import BasePlugin, PluginManager, PreToolCallDecision
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.engine import AgentModels, Engine, _apply_overrides, _is_yaml_value
from axc_agent_engine.planning.checkpointing import plan_from_state
from axc_agent_engine.planning.graph_runtime import PORGraphRuntime
from axc_agent_engine.planning.planner import Plan, PlanStep, validate_plan
from axc_agent_engine.planning.replanner import replan
from axc_agent_engine.planning.router import TransactionRouter
from axc_agent_engine.runtime.concurrency import ExecutionLimiter, RateLimiter, SessionExecutionGate
from axc_agent_engine.runtime.resources import (
	DuplicateResourceError,
	ResourceNotFoundError,
	ResourceRegistry,
	ResourceTypeError,
	ensure_resource_registry,
)
from axc_agent_engine.runtime.sandbox_local import LocalSubprocessExecutor
from axc_agent_engine.runtime.sandbox_models import CommandSpec
from axc_agent_engine.storage.in_memory import InMemoryKVStore, InMemoryMessageBus, InMemoryMessagePersistence, InMemorySpanStore, InMemoryVectorStore
from axc_agent_engine.tools.executor import execute_tool
from axc_agent_engine.tools.name_mapping import ToolNameMapper, ToolNameMappingConfig, ToolNameMappingError
from axc_agent_engine.tools.orchestrator import _argument_timeout, _effective_orchestration_timeout
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.runtime import tool_runtime
from axc_agent_engine.tools.tool_output import ToolOutput


def test_run_context_factory_and_options_copy_boundaries():
	from axc_agent_engine.core.run_context import call_context_factory, copy_run_options, normalize_run_context
	from axc_agent_engine.core.run_request import RunOptions

	approval_queue = object()
	options = {"approval_queue": approval_queue, "nested": {"v": 1}}
	copied = copy_run_options(options)
	options["nested"]["v"] = 2

	assert copied["approval_queue"] is approval_queue
	assert copied["nested"] == {"v": 1}
	assert call_context_factory(None, "factory") == {}
	assert call_context_factory(lambda a: {"a": a}, "factory", 3) == {"a": 3}
	with pytest.raises(TypeError, match="factory must return a dict"):
		call_context_factory(lambda: [], "factory")

	run_options, metadata = normalize_run_context(default_run_id="run-default")
	assert "run_id" not in run_options
	assert metadata["run_id"] == "run-default"
	assert RunOptions.from_dict({"stream": False, "stream_idle_timeout": "bad"}).stream is False


def test_resource_registry_rejects_bad_names_duplicates_and_types():
	registry = ResourceRegistry()
	with pytest.raises(ValueError, match="must not be empty"):
		registry.register("", object())
	registry.register("value", "x")
	with pytest.raises(DuplicateResourceError):
		registry.register("value", "y")
	registry.register("value", "y", replace=True)
	assert registry.require("value", str) == "y"
	with pytest.raises(ResourceTypeError):
		registry.require("value", int)
	with pytest.raises(ResourceNotFoundError):
		registry.require("missing")
	assert ensure_resource_registry(registry) is registry
	assert ensure_resource_registry({"a": 1}).require("a") == 1


@pytest.mark.asyncio
async def test_concurrency_limiters_timeout_and_release_paths():
	limiter = ExecutionLimiter(1, queue_timeout=0.01, name="unit")
	async with limiter.slot():
		with pytest.raises(ExecutionTimeoutError, match="unit concurrency"):
			async with limiter.slot():
				pass
	assert limiter.limit == 1

	gate = SessionExecutionGate(1, queue_timeout=0.01)
	async with gate.slot("s"):
		with pytest.raises(ExecutionTimeoutError, match="session 's'"):
			async with gate.slot("s"):
				pass
	async with gate.slot(""):
		pass

	rate = RateLimiter(requests_per_minute=1, queue_timeout=0.01)
	async with rate.slot():
		pass
	with pytest.raises(ExecutionTimeoutError, match="provider rate limit"):
		async with rate.slot():
			pass


@pytest.mark.asyncio
async def test_in_memory_storage_expiry_capacity_request_and_idle(monkeypatch):
	kv = InMemoryKVStore(max_size=2, ttl=1)
	now = [100.0]
	monkeypatch.setattr("axc_agent_engine.storage.in_memory.time.time", lambda: now[0])
	await kv.set("a", {"v": 1})
	await kv.set("b", {"v": 2})
	await kv.set("c", {"v": 3})
	assert await kv.get("a") is None
	now[0] = 102.0
	assert await kv.get("b") is None
	assert await kv.list_keys() == []

	messages = InMemoryMessagePersistence(max_sessions=1)
	await messages.save("s1", [{"role": "user"}])
	await messages.save("s2", [{"role": "assistant"}])
	assert await messages.load("s1") == []
	await messages.delete("s2")
	assert await messages.load("s2") == []

	spans = InMemorySpanStore(max_spans=1)
	await spans.save_span({"trace_id": "old", "session_id": "s"})
	await spans.save_span({"trace_id": "new", "session_id": "s"})
	assert await spans.query_by_trace("old") == []
	assert len(await spans.query_by_session("s", limit=1)) == 1

	vector = InMemoryVectorStore(max_entries=1)
	ids = await vector.add(["old"], [[1, 0]], [{}])
	await vector.add(["new"], [[0, 1]], [{}])
	await vector.delete(ids)
	assert [row["text"] for row in await vector.search([0, 1], top_k=2)] == ["new"]

	async def instant_timeout(awaitable, timeout):
		awaitable.close()
		raise asyncio.TimeoutError()

	monkeypatch.setattr("axc_agent_engine.storage.in_memory.asyncio.wait_for", instant_timeout)
	bus = InMemoryMessageBus(max_idle_rounds=1)
	subscription = bus.subscribe("ch")
	with pytest.raises(StopAsyncIteration):
		await subscription.__anext__()
	await bus.close()
	await subscription.aclose()


@pytest.mark.asyncio
async def test_message_bus_request_reply_and_cleanup():
	bus = InMemoryMessageBus()

	async def responder():
		async for message in bus.subscribe("ask"):
			await bus.publish(message["_reply_to"], {"ok": message["value"]})
			return

	task = asyncio.create_task(responder())
	await asyncio.sleep(0)
	assert await bus.request("ask", {"value": 7}, timeout=1) == {"ok": 7}
	await task


def test_message_store_prompt_and_plugin_context_edges():
	store = MessageStore()
	store.upsert_plugin_context("ignored")
	store.init_system_prompt("")
	assert store.get_all() == []
	store.init_system_prompt("system")
	store.upsert_plugin_context("ctx1")
	store.upsert_plugin_context("ctx2")
	store.set_at(99, {"role": "user"})
	assert store.get_all()[1]["content"].endswith("ctx2")
	assert store.get_recent(0) == []
	assert store.snapshot() == 2


@pytest.mark.asyncio
async def test_plugin_manager_strict_pre_tool_decision_and_failure_hook_details():
	class DecisionPlugin(BasePlugin):
		name = "decision"

		async def pre_tool_call(self, ctx, tool_name, arguments):
			return PreToolCallDecision(False, arguments, reason="no", details={"kind": "test"})

	manager = PluginManager([DecisionPlugin()])
	decision = await manager.apply_pre_tool_call(ExecutionContext(), "tool", {})
	assert decision.plugin_name == "decision"
	assert decision.details == {"kind": "test"}

	class BadArgsPlugin(BasePlugin):
		name = "bad_args"

		async def pre_tool_call(self, ctx, tool_name, arguments):
			return True, []

	with pytest.raises(TypeError, match="arguments must be a dict"):
		await PluginManager([BadArgsPlugin()]).apply_pre_tool_call(ExecutionContext(), "tool", {})


def test_engine_override_and_model_boundaries():
	with pytest.raises(Exception, match="default is required"):
		AgentModels(default=None)
	assert AgentModels(default=object()).utility_or_default is not None
	assert _is_yaml_value({"a": [1, None, True]})
	assert not _is_yaml_value({"a": object()})

	raw = {"runtime": {"workspace": "/tmp"}, "plugins": {"p": {"enabled": True}}}
	_apply_overrides(raw, {"runtime.workspace": "/work", "plugins.p.enabled": False})
	assert raw["runtime"]["workspace"] == "/work"
	assert raw["plugins"]["p"]["enabled"] is False
	for overrides in (
		{"plugins.knowledge.index": "x"},
		{"runtime..workspace": "x"},
		{"missing.value": "x"},
		{"runtime.workspace.path": "x"},
		{"runtime.workspace": object()},
	):
		with pytest.raises(SchemaError):
			_apply_overrides({"runtime": {"workspace": "/tmp"}, "plugins": {}}, overrides)

	provider = SimpleNamespace(tool_name_mapping=ToolNameMappingConfig())
	assert Engine._provider_tool_name_mapping(provider) == provider.tool_name_mapping
	assert Engine._provider_tool_name_mapping(SimpleNamespace(tool_name_mapping="bad")) is None


@pytest.mark.asyncio
async def test_sandbox_local_rejects_missing_command_and_timeout_cleanup(tmp_path):
	executor = LocalSubprocessExecutor()
	with pytest.raises(ValueError, match="command is required"):
		await executor.run(CommandSpec(use_shell=True))
	with pytest.raises(ValueError, match="argv is required"):
		await executor.run(CommandSpec())
	result = await executor.run(CommandSpec(
		argv=["/bin/sh", "-c", "sleep 1"],
		cwd=str(tmp_path),
		timeout=0.01,
	))
	assert result.timed_out is True
	assert result.exit_code == -1


@pytest.mark.asyncio
async def test_tool_executor_validation_retry_timeout_and_contract(monkeypatch):
	missing_execute = ToolDefinition(name="missing")
	result = await execute_tool(missing_execute, {}, "tc")
	assert not result.success and "no execute" in result.error

	strict = ToolDefinition(
		name="strict",
		parameters={"type": "object", "properties": {"n": {"type": "integer", "minimum": 2, "maximum": 3}}},
		execute=lambda args, ctx: ToolOutput.text("ok"),
	)
	assert "below minimum" in (await execute_tool(strict, {"n": 1}, "tc")).error
	assert "above maximum" in (await execute_tool(strict, {"n": 4}, "tc")).error

	calls = 0

	async def flaky(args, ctx):
		nonlocal calls
		calls += 1
		if calls == 1:
			raise RuntimeError("temporary network reset")
		return ToolOutput.text("ok")

	async def no_sleep(delay):
		return None

	monkeypatch.setattr("axc_agent_engine.tools.executor.asyncio.sleep", no_sleep)
	retry_tool = ToolDefinition(name="retry", is_read_only=True, execute=flaky)
	assert (await execute_tool(retry_tool, {}, "tc")).success is True
	assert calls == 2

	async def bad_contract(args, ctx):
		return "bad"

	with pytest.raises(TypeError, match="ToolOutput"):
		await execute_tool(ToolDefinition(name="contract", execute=bad_contract), {}, "tc")

	async def timeout(args, ctx):
		raise asyncio.TimeoutError()

	timed_out = await execute_tool(ToolDefinition(name="timeout", execute=timeout, timeout=3), {}, "tc")
	assert "timeout (3s)" in timed_out.error


def test_tool_name_mapping_error_and_clear_paths():
	mapper = ToolNameMapper(ToolNameMappingConfig(collision="error"))
	assert mapper.encode("a.b") == "a_b"
	with pytest.raises(ToolNameMappingError):
		mapper.encode("a/b")
	mapper.clear()
	assert mapper.decode("a_b") == "a_b"
	with pytest.raises(ToolNameMappingError, match="does not match pattern"):
		ToolNameMapper(ToolNameMappingConfig(pattern=r"^x+$")).encode("abc")


def test_tool_orchestration_timeout_helpers():
	ctx = ExecutionContext()
	ctx.runtime.step_timeout = 3
	registry = ToolRegistry()
	registry.register(ToolDefinition(name="agent_call", execute=lambda args, ctx: ToolOutput.text("ok")))
	runtime = tool_runtime({"name": "agent_call", "arguments": {"timeout": 10}, "id": "tc"}, ctx, registry)
	assert _effective_orchestration_timeout(ctx, runtime) == 300
	runtime.arguments = {"timeout": "bad"}
	assert _argument_timeout(runtime.arguments) == 0


def test_planning_validation_and_routing_edges():
	for plan in (
		Plan(goal="", steps=[]),
		Plan(goal="g", steps=[PlanStep(step_id=0, description="x")]),
		Plan(goal="g", steps=[PlanStep(step_id=1, description="")]),
		Plan(goal="g", steps=[PlanStep(step_id=1, description="x", depends_on=[1])]),
		Plan(goal="g", steps=[PlanStep(step_id=1, description="x", depends_on=[99])]),
	):
		with pytest.raises(SchemaError):
			validate_plan(plan)

	assert TransactionRouter("react_only").route({"content": "{\"goal\":\"g\",\"steps\":[]}"}).action == "final_answer"
	assert TransactionRouter("auto").route({"tool_calls": [{"id": "tc"}], "content": "no plan"}).action == "tool_calls"


@pytest.mark.asyncio
async def test_planning_checkpoint_and_replan_error_edges():
	assert plan_from_state({"payload": {"plan": {"goal": "g", "steps": [{"step_id": 1, "status": "bad"}]}}}).steps[0].status.value == "pending"
	assert plan_from_state({"payload": {"plan": {"goal": "g", "steps": ["bad"]}}}).steps == []

	class BadLLM:
		async def ask(self, prompt):
			raise RuntimeError("down")

	plan = Plan(goal="g", steps=[
		PlanStep(step_id=1, description="a", status="done"),
		PlanStep(step_id=2, description="b", depends_on=[1]),
		PlanStep(step_id=3, description="c", depends_on=[2]),
	])
	plan.steps[1].status = plan.steps[1].status.FAILED
	result = await replan(plan, 2, BadLLM())
	assert result.replan_count == 1
	assert result.steps[2].status.value == "skipped"


@pytest.mark.asyncio
async def test_por_graph_runtime_surfaces_graph_errors(monkeypatch):
	runtime = PORGraphRuntime(service=object())

	class BadGraph:
		async def run(self, state, deps):
			raise RuntimeError("graph down")

	runtime._graph = BadGraph()
	events = [event async for event in runtime.run(Plan(goal="g"), "user")]
	assert events[0].content == "graph down"
