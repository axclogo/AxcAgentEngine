"""Tests for StreamAggregator with LLMStreamChunk."""
import asyncio
import pytest
from axc_agent_engine.core.stream_aggregator import StreamAggregator
from axc_agent_engine.core.errors import CancelledError
from axc_agent_engine.core.schema import LLMStreamChunk, LLMUsage


async def _make_stream(chunks: list[LLMStreamChunk]):
	for chunk in chunks:
		yield chunk


class TestStreamAggregator:
	@pytest.mark.asyncio
	async def test_empty_stream(self):
		agg = StreamAggregator()
		result = await agg.aggregate(_make_stream([]), idle_timeout=10)
		assert result.message == {"role": "assistant", "content": ""}
		assert result.thinking_content == ""
		assert result.has_content is False

	@pytest.mark.asyncio
	async def test_content_aggregation(self):
		chunks = [
			LLMStreamChunk(content_delta="Hello"),
			LLMStreamChunk(content_delta=" world"),
		]
		agg = StreamAggregator()
		result = await agg.aggregate(_make_stream(chunks), idle_timeout=10)
		assert result.message["content"] == "Hello world"
		assert result.has_content is True

	@pytest.mark.asyncio
	async def test_thinking_aggregation(self):
		chunks = [
			LLMStreamChunk(thinking_delta="Let me think"),
			LLMStreamChunk(thinking_delta=" about this"),
			LLMStreamChunk(content_delta="Answer"),
		]
		agg = StreamAggregator()
		result = await agg.aggregate(_make_stream(chunks), idle_timeout=10)
		assert result.thinking_content == "Let me think about this"
		assert result.message["content"] == "Answer"

	@pytest.mark.asyncio
	async def test_tool_calls_aggregation(self):
		chunks = [
			LLMStreamChunk(tool_call_delta={"index": 0, "id": "tc-1", "function": {"name": "file_read", "arguments": ""}}),
			LLMStreamChunk(tool_call_delta={"index": 0, "function": {"arguments": '{"path":'}}),
			LLMStreamChunk(tool_call_delta={"index": 0, "function": {"arguments": '"a.txt"}'}}),
		]
		previews = []
		async def on_delta(event_type, content, metadata):
			if event_type == "tool_args_delta":
				previews.append((content, metadata))
		agg = StreamAggregator()
		result = await agg.aggregate(_make_stream(chunks), idle_timeout=10, on_delta=on_delta)
		assert "tool_calls" in result.message
		tc = result.message["tool_calls"][0]
		assert tc["id"] == "tc-1"
		assert tc["function"]["name"] == "file_read"
		assert tc["function"]["arguments"] == '{"path":"a.txt"}'
		assert previews[-1][1]["arguments_preview"] == '{"path":"a.txt"}'

	@pytest.mark.asyncio
	async def test_multiple_tool_calls(self):
		chunks = [
			LLMStreamChunk(tool_call_delta={"tool_calls": [
				{"index": 0, "id": "tc-1", "function": {"name": "a", "arguments": "{}"}},
				{"index": 1, "id": "tc-2", "function": {"name": "b", "arguments": "{}"}},
			]}),
		]
		agg = StreamAggregator()
		result = await agg.aggregate(_make_stream(chunks), idle_timeout=10)
		assert len(result.message["tool_calls"]) == 2

	@pytest.mark.asyncio
	async def test_usage_aggregation(self):
		chunks = [
			LLMStreamChunk(content_delta="hi", usage=LLMUsage(input_tokens=10, output_tokens=5)),
		]
		agg = StreamAggregator()
		result = await agg.aggregate(_make_stream(chunks), idle_timeout=10)
		assert result.usage_input == 10
		assert result.usage_output == 5

	@pytest.mark.asyncio
	async def test_cached_tokens(self):
		chunks = [
			LLMStreamChunk(usage=LLMUsage(input_tokens=100, output_tokens=0, cached_tokens=50)),
			LLMStreamChunk(content_delta="x"),
		]
		agg = StreamAggregator()
		result = await agg.aggregate(_make_stream(chunks), idle_timeout=10)
		assert result.cached_tokens == 50

	@pytest.mark.asyncio
	async def test_idle_timeout_returns_partial(self):
		async def slow_stream():
			yield LLMStreamChunk(content_delta="a")
			await asyncio.sleep(10)
			yield LLMStreamChunk(content_delta="b")
		agg = StreamAggregator()
		result = await agg.aggregate(slow_stream(), idle_timeout=0.1)
		assert result.message["content"] == "a"
		assert result.partial is True

	@pytest.mark.asyncio
	async def test_idle_timeout_no_content_raises(self):
		async def empty_slow_stream():
			await asyncio.sleep(10)
			yield LLMStreamChunk(content_delta="b")
		agg = StreamAggregator()
		with pytest.raises(CancelledError):
			await agg.aggregate(empty_slow_stream(), idle_timeout=0.1)

	@pytest.mark.asyncio
	async def test_max_chunks_returns_partial(self):
		async def many_chunks():
			while True:
				yield LLMStreamChunk(content_delta="x")
		agg = StreamAggregator()
		result = await agg.aggregate(many_chunks(), idle_timeout=60)
		assert result.partial is True
		assert len(result.message["content"]) > 0
