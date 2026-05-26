"""Tests for real-time streaming with standardized LLMStreamChunk/LLMResponse."""
import asyncio
import time
import pytest
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionState
from axc_agent_engine.core.executor import Executor
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.events import EventType
from axc_agent_engine.core.schema import LLMStreamChunk, LLMResponse, LLMMessage, LLMUsage
from axc_agent_engine.tools.registry import ToolRegistry


class SlowProvider:
	@property
	def model(self):
		return "test-slow"

	async def stream(self, messages, tools=None, **kwargs):
		yield LLMStreamChunk(content_delta="A")
		await asyncio.sleep(0.05)
		yield LLMStreamChunk(content_delta="B")

	async def chat(self, messages, tools=None, **kwargs):
		raise AssertionError("stream path expected")

	async def ask(self, prompt, **kwargs):
		return ""

	async def close(self):
		pass


class TestRealtimeStream:
	@pytest.mark.asyncio
	async def test_stream_deltas_are_realtime_and_ordered(self):
		pm = PluginManager([])
		ctx = ExecutionContext(config=ExecutionConfig(stream=True), state=ExecutionState())
		executor = Executor(
			llm_caller=LLMCaller(SlowProvider(), None, pm),
			registry=ToolRegistry(),
			plugin_manager=pm,
			ctx=ctx,
		)
		events = []
		start = time.perf_counter()
		first_delta_time = None
		second_delta_time = None
		async for event in executor.run_stream("hi"):
			events.append(event)
			if event.type == EventType.STREAM_DELTA and event.content == "A":
				first_delta_time = time.perf_counter() - start
			if event.type == EventType.STREAM_DELTA and event.content == "B":
				second_delta_time = time.perf_counter() - start
		types = [e.type for e in events]
		assert EventType.STREAM_START in types
		assert EventType.STREAM_DELTA in types
		start_idx = types.index(EventType.STREAM_START)
		first_delta_idx = types.index(EventType.STREAM_DELTA)
		assert start_idx < first_delta_idx
		deltas = [e.content for e in events if e.type == EventType.STREAM_DELTA]
		assert deltas == ["A", "B"]
		assert first_delta_time is not None
		assert second_delta_time is not None
		assert first_delta_time < 0.05
		assert second_delta_time >= 0.04
		assert types[-1] == EventType.DONE

	@pytest.mark.asyncio
	async def test_stream_partial_output_does_not_fallback(self):
		import httpx

		class PrimaryProvider:
			@property
			def model(self):
				return "primary"
			async def stream(self, messages, tools=None, **kwargs):
				yield LLMStreamChunk(content_delta="A")
				raise httpx.NetworkError("broken after partial")
			async def chat(self, messages, tools=None, **kwargs):
				raise AssertionError("stream path expected")
			async def ask(self, prompt, **kwargs):
				return ""
			async def close(self):
				pass

		class FallbackProvider:
			called = False
			@property
			def model(self):
				return "fallback"
			async def stream(self, messages, tools=None, **kwargs):
				FallbackProvider.called = True
				yield LLMStreamChunk(content_delta="fallback")
			async def chat(self, messages, tools=None, **kwargs):
				raise AssertionError("stream path expected")
			async def ask(self, prompt, **kwargs):
				return ""
			async def close(self):
				pass

		pm = PluginManager([])
		fallback = FallbackProvider()
		ctx = ExecutionContext(config=ExecutionConfig(stream=True), state=ExecutionState())
		executor = Executor(
			llm_caller=LLMCaller(PrimaryProvider(), fallback, pm),
			registry=ToolRegistry(),
			plugin_manager=pm,
			ctx=ctx,
		)
		events = []
		async for event in executor.run_stream("hi"):
			events.append(event)
		assert FallbackProvider.called is False
		deltas = [e.content for e in events if e.type == EventType.STREAM_DELTA]
		assert deltas == ["A"]
		errors = [e.content for e in events if e.type == EventType.ERROR]
		assert errors
		assert "partial output" in errors[-1].lower()

	@pytest.mark.asyncio
	async def test_non_stream_mode_still_works(self):
		class SyncProvider:
			@property
			def model(self):
				return "test-sync"
			async def chat(self, messages, tools=None, **kwargs):
				return LLMResponse(message=LLMMessage(content="hello"), usage=LLMUsage(input_tokens=5, output_tokens=3))
			async def stream(self, messages, tools=None, **kwargs):
				raise AssertionError("should not be called")
			async def ask(self, prompt, **kwargs):
				return ""
			async def close(self):
				pass

		pm = PluginManager([])
		ctx = ExecutionContext(config=ExecutionConfig(stream=False), state=ExecutionState())
		executor = Executor(
			llm_caller=LLMCaller(SyncProvider(), None, pm),
			registry=ToolRegistry(),
			plugin_manager=pm,
			ctx=ctx,
		)
		events = []
		async for event in executor.run_stream("hi"):
			events.append(event)
		types = [e.type for e in events]
		assert EventType.DONE in types
		done_event = next(e for e in events if e.type == EventType.DONE)
		assert done_event.content == "hello"
