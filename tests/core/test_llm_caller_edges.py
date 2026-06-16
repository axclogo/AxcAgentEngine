import asyncio

import httpx
import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext
from axc_agent_engine.core.errors import ProviderContractError, ProviderError, RetryableProviderError
from axc_agent_engine.core.events import EventType
from axc_agent_engine.core.llm_caller import LLMCaller, StreamEventEmitter, StreamUsageReporter
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMStreamChunk, LLMUsage
from axc_agent_engine.core.stream_sink import QueueStreamSink


class _Provider:
	model = "p"
	tool_name_mapping = None

	def __init__(self, *, chat_result=None, chat_error=None, chunks=None, stream_error=None):
		self.chat_result = chat_result
		self.chat_error = chat_error
		self.chunks = chunks or []
		self.stream_error = stream_error
		self.chat_calls = []
		self.stream_calls = []

	async def chat(self, messages, tools=None, **kwargs):
		self.chat_calls.append((messages, tools, kwargs))
		if self.chat_error:
			raise self.chat_error
		return self.chat_result

	async def stream(self, messages, tools=None, **kwargs):
		self.stream_calls.append((messages, tools, kwargs))
		for chunk in self.chunks:
			yield chunk
		if self.stream_error:
			raise self.stream_error


@pytest.mark.asyncio
async def test_sync_response_cache_hit_and_llm_options_are_forwarded(monkeypatch):
	provider = _Provider(chat_result=LLMResponse(
		message=LLMMessage(role="assistant", content="ok"),
		usage=LLMUsage(input_tokens=3, output_tokens=2, cached_tokens=1),
		raw={"cache_type": "prompt"},
	))
	ctx = ExecutionContext(config=ExecutionConfig(stream=False))
	ctx.runtime.llm_options = {"temperature": 0.2, "unknown": "ignored"}
	caller = LLMCaller(provider, None, PluginManager([]))

	message, events = await caller.call(ctx, [{"role": "user", "content": "hi", "round": 1}], [{"type": "function"}])

	assert message["content"] == "ok"
	assert provider.chat_calls[0][0] == [{"role": "user", "content": "hi"}]
	assert provider.chat_calls[0][2] == {"parallel_tool_calls": True, "temperature": 0.2}
	assert [event.type for event in events] == [EventType.CACHE_HIT, EventType.COST_UPDATE]
	assert events[0].metadata == {"cached_tokens": 1, "cache_type": "prompt"}


@pytest.mark.asyncio
async def test_sync_provider_contract_error_is_not_hidden_by_fallback(monkeypatch):
	primary = _Provider(chat_result={"content": "bad"})
	fallback = _Provider(chat_result=LLMResponse(message=LLMMessage(role="assistant", content="fallback")))
	caller = LLMCaller(primary, fallback, PluginManager([]))

	with pytest.raises(ProviderContractError):
		await caller.call(ExecutionContext(config=ExecutionConfig(stream=False)), [{"role": "user", "content": "hi"}], None)
	assert fallback.chat_calls == []


@pytest.mark.asyncio
async def test_retry_sleep_and_final_no_fallback_error(monkeypatch):
	sleep_calls = []

	async def fake_sleep(delay):
		sleep_calls.append(delay)

	monkeypatch.setattr("axc_agent_engine.core.llm_caller.asyncio.sleep", fake_sleep)
	monkeypatch.setattr("axc_agent_engine.core.llm_caller.random.uniform", lambda a, b: 0)
	primary = _Provider(chat_error=RetryableProviderError("temporary"))
	caller = LLMCaller(primary, None, PluginManager([]))

	with pytest.raises(RetryableProviderError):
		await caller.call(ExecutionContext(config=ExecutionConfig(stream=False)), [{"role": "user", "content": "hi"}], None)
	assert sleep_calls == [1.0]
	assert len(primary.chat_calls) == 2


@pytest.mark.asyncio
async def test_stream_partial_output_blocks_fallback_and_wraps_error():
	primary = _Provider(chunks=[LLMStreamChunk(content_delta="partial")], stream_error=httpx.NetworkError("net"))
	fallback = _Provider(chunks=[LLMStreamChunk(content_delta="fallback")])
	caller = LLMCaller(primary, fallback, PluginManager([]))
	ctx = ExecutionContext(config=ExecutionConfig(stream=True))

	with pytest.raises(ProviderError, match="partial output"):
		await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
	assert fallback.stream_calls == []


@pytest.mark.asyncio
async def test_stream_sink_receives_live_events_and_usage_metadata():
	queue = asyncio.Queue()
	sink = QueueStreamSink(queue)
	provider = _Provider(chunks=[
		LLMStreamChunk(thinking_delta="think"),
		LLMStreamChunk(content_delta="answer"),
		LLMStreamChunk(
			usage=LLMUsage(input_tokens=4, output_tokens=5, cached_tokens=2),
			metadata={"tier": "cached"},
		),
	])
	ctx = ExecutionContext(config=ExecutionConfig(stream=True, thinking="always"))
	caller = LLMCaller(provider, None, PluginManager([]))

	message, events = await caller.call(ctx, [{"role": "user", "content": "hi"}], None, stream_sink=sink)

	assert message["content"] == "answer"
	assert provider.stream_calls[0][2]["thinking"] == "always"
	assert [event.type for event in events] == [EventType.CACHE_HIT, EventType.COST_UPDATE]
	assert events[0].metadata == {"cached_tokens": 2, "tier": "cached"}
	live = []
	while not queue.empty():
		live.append((await queue.get()).type)
	assert live == [
		EventType.THINKING_START,
		EventType.THINKING_DELTA,
		EventType.THINKING_END,
		EventType.STREAM_START,
		EventType.STREAM_DELTA,
	]


@pytest.mark.asyncio
async def test_stream_event_emitter_without_sink_stores_tool_preview_and_finishes_noop():
	ctx = ExecutionContext()
	emitter = StreamEventEmitter(ctx, None)
	await emitter.on_delta("tool_args_delta", "x", {
		"tool_name": "tool",
		"tool_call_id": "tc",
		"arguments_preview": "{\"a\"",
		"index": "2",
	})
	await emitter.finish_thinking("ignored")
	assert emitter.preview_events[0].type == EventType.TOOL_ARGS_PREVIEW
	assert emitter.preview_events[0].metadata["index"] == 2


def test_stream_usage_reporter_no_usage_is_noop():
	ctx = ExecutionContext()
	result = type("Result", (), {
		"usage_input": 0,
		"usage_output": 0,
		"cached_tokens": 0,
		"usage_metadata": {},
	})()
	assert StreamUsageReporter().apply_usage(ctx, result) == []
