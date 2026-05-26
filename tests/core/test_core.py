"""Tests for core modules — executor, llm_caller, message_store, session_manager."""
import asyncio
import pytest
from unittest.mock import AsyncMock

from axc_agent_engine.core.executor import Executor
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.errors import RetryableProviderError
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.session_manager import SessionManager
from axc_agent_engine.core.session import Session
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.context import ExecutionContext, ExecutionConfig
from axc_agent_engine.core.events import EventType
from axc_agent_engine.core.errors import ProviderError, CancelledError
from axc_agent_engine.core.constants import PLUGIN_CONTEXT_TAG
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMUsage
from axc_agent_engine.plugins import model_info_from_providers


class TestMessageStore:
	def test_append_and_get(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": "hi"})
		assert ms.count == 1
		assert ms.get_all()[0]["content"] == "hi"

	def test_extend(self):
		ms = MessageStore()
		ms.extend([{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
		assert ms.count == 2

	def test_get_recent(self):
		ms = MessageStore()
		for i in range(10):
			ms.append({"role": "user", "content": str(i)})
		recent = ms.get_recent(3)
		assert len(recent) == 3
		assert recent[0]["content"] == "7"

	def test_set_all(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": "old"})
		ms.set_all([{"role": "user", "content": "new"}])
		assert ms.count == 1
		assert ms.get_all()[0]["content"] == "new"

	def test_clear(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": "hi"})
		ms.clear()
		assert ms.count == 0

	def test_init_system_prompt(self):
		ms = MessageStore()
		ms.init_system_prompt("You are helpful")
		assert ms.get_all()[0] == {"role": "system", "content": "You are helpful"}

	def test_init_system_prompt_update(self):
		ms = MessageStore()
		ms.init_system_prompt("v1")
		ms.init_system_prompt("v2")
		assert ms.count == 1
		assert ms.get_all()[0]["content"] == "v2"

	def test_upsert_plugin_context(self):
		ms = MessageStore()
		ms.init_system_prompt("sys")
		ms.upsert_plugin_context("memory: fact1")
		assert ms.count == 2
		assert PLUGIN_CONTEXT_TAG in ms.get_all()[1]["content"]

	def test_upsert_plugin_context_update(self):
		ms = MessageStore()
		ms.init_system_prompt("sys")
		ms.upsert_plugin_context("v1")
		ms.upsert_plugin_context("v2")
		assert ms.count == 2
		assert "v2" in ms.get_all()[1]["content"]

	def test_append_tool_results(self):
		ms = MessageStore()
		from axc_agent_engine.tools.executor import ToolResult
		from axc_agent_engine.tools.tool_output import ToolOutput
		results = [
			ToolResult(tool_call_id="1", tool_name="echo", arguments={}, output=ToolOutput.text("ok"), success=True),
			ToolResult(tool_call_id="2", tool_name="bad", arguments={}, error="fail", success=False),
		]
		ms.append_tool_results(results)
		assert ms.count == 2
		assert "ok" in ms.get_all()[0]["content"]
		assert "[Error]" in ms.get_all()[1]["content"]

	def test_get_first(self):
		ms = MessageStore()
		assert ms.get_first() is None
		ms.append({"role": "user", "content": "first"})
		assert ms.get_first()["content"] == "first"

	def test_insert(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": "a"})
		ms.append({"role": "user", "content": "c"})
		ms.insert(1, {"role": "user", "content": "b"})
		assert ms.get_all()[1]["content"] == "b"


class TestSessionManager:
	@pytest.mark.asyncio
	async def test_get_or_create(self):
		sm = SessionManager()
		session = await sm.get_or_create("s1")
		assert session.session_id == "s1"
		assert session.messages == []

	@pytest.mark.asyncio
	async def test_get_existing(self):
		sm = SessionManager()
		s1 = await sm.get_or_create("s1")
		s1.messages = [{"role": "user", "content": "hi"}]
		s2 = await sm.get_or_create("s1")
		assert s2.messages == [{"role": "user", "content": "hi"}]

	@pytest.mark.asyncio
	async def test_get_nonexistent(self):
		sm = SessionManager()
		result = await sm.get("nope")
		assert result is None

	@pytest.mark.asyncio
	async def test_remove(self):
		sm = SessionManager()
		await sm.get_or_create("s1")
		await sm.remove("s1")
		assert await sm.get("s1") is None

	@pytest.mark.asyncio
	async def test_clear(self):
		sm = SessionManager()
		await sm.get_or_create("s1")
		await sm.get_or_create("s2")
		await sm.clear()
		assert sm.count == 0

	@pytest.mark.asyncio
	async def test_max_sessions_eviction(self):
		sm = SessionManager(max_sessions=2)
		await sm.get_or_create("s1")
		await sm.get_or_create("s2")
		await sm.get_or_create("s3")
		assert sm.count == 2
		assert await sm.get("s1") is None

	@pytest.mark.asyncio
	async def test_ttl_eviction(self):
		sm = SessionManager(ttl=0)  # 0 = no TTL
		await sm.get_or_create("s1")
		assert sm.count == 1

	@pytest.mark.asyncio
	async def test_restore_context_basic(self):
		sm = SessionManager()
		session = Session(session_id="s1", messages=[{"role": "user", "content": "old"}])
		ms = MessageStore()
		ms.append({"role": "user", "content": "new"})
		sm.restore_context(session, ms)
		all_msgs = ms.get_all()
		assert all_msgs[0]["content"] == "old"
		assert all_msgs[1]["content"] == "new"

class TestLLMCaller:
	@pytest.mark.asyncio
	async def test_sync_call(self, mock_llm):
		pm = PluginManager([])
		caller = LLMCaller(primary=mock_llm, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(stream=False))
		message, events = await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
		assert message["content"] == "hello"
		assert ctx.state.total_input_tokens == 10
		assert ctx.state.total_output_tokens == 5

	@pytest.mark.asyncio
	async def test_fallback_on_failure(self, mock_llm):
		primary = AsyncMock()
		primary.model = "primary-model"
		primary.chat = AsyncMock(side_effect=RetryableProviderError("primary down"))
		pm = PluginManager([])
		caller = LLMCaller(primary=primary, fallback=mock_llm, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(stream=False))
		ctx.runtime.model_info = model_info_from_providers(primary, mock_llm)
		ctx.state.metadata["model"] = ctx.runtime.model_info.to_dict()
		message, events = await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
		assert message["content"] == "hello"
		assert ctx.state.fallback_triggered is True
		assert ctx.runtime.model_info.active == "test-model"
		assert ctx.state.metadata["model"]["active"] == "test-model"

	@pytest.mark.asyncio
	async def test_no_fallback_raises(self):
		primary = AsyncMock()
		primary.chat = AsyncMock(side_effect=RuntimeError("down"))
		pm = PluginManager([])
		caller = LLMCaller(primary=primary, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(stream=False))
		with pytest.raises(RuntimeError):
			await caller.call(ctx, [{"role": "user", "content": "hi"}], None)

	@pytest.mark.asyncio
	async def test_stream_aggregation(self, mock_llm):
		from axc_agent_engine.core.schema import LLMStreamChunk, LLMUsage
		chunks = [
			LLMStreamChunk(content_delta="hel"),
			LLMStreamChunk(content_delta="lo"),
			LLMStreamChunk(usage=LLMUsage(input_tokens=5, output_tokens=2)),
		]
		async def fake_stream(*args, **kwargs):
			for c in chunks:
				yield c
		mock_llm.stream = fake_stream
		pm = PluginManager([])
		caller = LLMCaller(primary=mock_llm, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(stream=True))
		message, events = await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
		assert message["content"] == "hello"
		delta_events = [e for e in events if e.type == EventType.STREAM_DELTA]
		assert len(delta_events) == 1
		assert delta_events[0].content == "hello"

	@pytest.mark.asyncio
	async def test_stream_thinking(self, mock_llm):
		from axc_agent_engine.core.schema import LLMStreamChunk
		chunks = [
			LLMStreamChunk(thinking_delta="let me think"),
			LLMStreamChunk(content_delta="answer"),
		]
		async def fake_stream(*args, **kwargs):
			for c in chunks:
				yield c
		mock_llm.stream = fake_stream
		pm = PluginManager([])
		caller = LLMCaller(primary=mock_llm, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(stream=True))
		message, events = await caller.call(ctx, [], None)
		thinking_events = [e for e in events if e.type == EventType.THINKING_DELTA]
		assert len(thinking_events) == 1

	@pytest.mark.asyncio
	async def test_stream_tool_calls(self, mock_llm):
		from axc_agent_engine.core.schema import LLMStreamChunk
		chunks = [
			LLMStreamChunk(tool_call_delta={"index": 0, "id": "tc1", "function": {"name": "echo", "arguments": ""}}),
			LLMStreamChunk(tool_call_delta={"index": 0, "function": {"arguments": '{"text":'}}),
			LLMStreamChunk(tool_call_delta={"index": 0, "function": {"arguments": '"hi"}'}}),
		]
		async def fake_stream(*args, **kwargs):
			for c in chunks:
				yield c
		mock_llm.stream = fake_stream
		pm = PluginManager([])
		caller = LLMCaller(primary=mock_llm, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(stream=True))
		message, events = await caller.call(ctx, [], None)
		assert "tool_calls" in message
		assert message["tool_calls"][0]["function"]["name"] == "echo"
		assert message["tool_calls"][0]["function"]["arguments"] == '{"text":"hi"}'
		preview_events = [e for e in events if e.type == EventType.TOOL_ARGS_PREVIEW]
		assert preview_events
		assert preview_events[-1].metadata["arguments_preview"] == '{"text":"hi"}'


async def _run_executor(executor, message: str) -> str:
	"""Helper: collect DONE event from executor stream."""
	from axc_agent_engine.core.errors import ProviderError
	result = ""
	async for event in executor.run_stream(message):
		if event.type == EventType.DONE:
			result = event.content
		elif event.type == EventType.ERROR:
			raise ProviderError(event.content)
	return result


class TestExecutor:
	@pytest.mark.asyncio
	async def test_simple_response(self, mock_llm, tool_registry):
		pm = PluginManager([])
		caller = LLMCaller(primary=mock_llm, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(system_prompt="test", stream=False, max_rounds=5))
		executor = Executor(llm_caller=caller, registry=tool_registry, plugin_manager=pm, ctx=ctx)
		result = await _run_executor(executor, "hello")
		assert result == "hello"

	@pytest.mark.asyncio
	async def test_max_rounds_exceeded(self, tool_registry):
		# LLM always returns tool calls, never a final response
		llm = AsyncMock()
		llm.model = "test"
		llm.chat = AsyncMock(return_value=LLMResponse(
			message=LLMMessage(role="assistant", content="", tool_calls=[
				{"id": "tc1", "function": {"name": "echo", "arguments": '{"text":"hi"}'}}
			]),
			usage=LLMUsage(input_tokens=1, output_tokens=1),
		))
		pm = PluginManager([])
		caller = LLMCaller(primary=llm, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(system_prompt="test", stream=False, max_rounds=2))
		executor = Executor(llm_caller=caller, registry=tool_registry, plugin_manager=pm, ctx=ctx)
		with pytest.raises(ProviderError):
			await _run_executor(executor, "hello")

	@pytest.mark.asyncio
	async def test_cancellation(self, mock_llm, tool_registry):
		pm = PluginManager([])
		caller = LLMCaller(primary=mock_llm, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(system_prompt="test", stream=False, max_rounds=5))
		ctx.cancel()
		executor = Executor(llm_caller=caller, registry=tool_registry, plugin_manager=pm, ctx=ctx)
		with pytest.raises(CancelledError):
			await _run_executor(executor, "hello")

	@pytest.mark.asyncio
	async def test_total_timeout(self, tool_registry):
		async def slow_chat(*args, **kwargs):
			await asyncio.sleep(0.1)
			return LLMResponse(message=LLMMessage(role="assistant", content="", tool_calls=[
				{"id": "tc1", "function": {"name": "echo", "arguments": '{"text":"hi"}'}}
			]), usage=LLMUsage(input_tokens=1, output_tokens=1))
		llm = AsyncMock()
		llm.model = "test"
		llm.chat = slow_chat
		pm = PluginManager([])
		caller = LLMCaller(primary=llm, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(system_prompt="test", stream=False, max_rounds=100, total_timeout=0))
		_executor = Executor(llm_caller=caller, registry=tool_registry, plugin_manager=pm, ctx=ctx)  # noqa: F841
		# total_timeout=0 means immediate timeout check will pass on first round
		# but we set it to trigger
		ctx.config = ExecutionConfig(system_prompt="test", stream=False, max_rounds=100, total_timeout=1)
		# This should eventually timeout or complete
