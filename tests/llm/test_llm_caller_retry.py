"""Tests for LLMCaller retry/fallback with standardized LLMResponse/LLMStreamChunk."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.context import ExecutionContext, ExecutionConfig, ExecutionState
from axc_agent_engine.core.errors import RetryableProviderError
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.schema import LLMResponse, LLMMessage, LLMUsage, LLMStreamChunk


def _make_ctx(stream=True):
	return ExecutionContext(
		config=ExecutionConfig(stream=stream, thinking="never"),
		state=ExecutionState(),
	)


def _make_response(content="ok"):
	return LLMResponse(message=LLMMessage(content=content), usage=LLMUsage(input_tokens=5, output_tokens=3))


class TestLLMCallerRetry:
	@pytest.mark.asyncio
	async def test_sync_fallback_on_error(self):
		primary = MagicMock()
		primary.chat = AsyncMock(side_effect=RetryableProviderError("primary down"))
		fallback = MagicMock()
		fallback.chat = AsyncMock(return_value=_make_response("from fallback"))
		pm = PluginManager([])
		caller = LLMCaller(primary=primary, fallback=fallback, plugin_manager=pm)
		ctx = _make_ctx(stream=False)
		msg, events = await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
		assert msg["content"] == "from fallback"
		assert ctx.state.fallback_triggered is True

	@pytest.mark.asyncio
	async def test_sync_no_fallback_raises(self):
		primary = MagicMock()
		primary.chat = AsyncMock(side_effect=Exception("primary down"))
		pm = PluginManager([])
		caller = LLMCaller(primary=primary, fallback=None, plugin_manager=pm)
		ctx = _make_ctx(stream=False)
		with pytest.raises(Exception, match="primary down"):
			await caller.call(ctx, [{"role": "user", "content": "hi"}], None)

	@pytest.mark.asyncio
	async def test_stream_retry_on_network_error(self):
		import httpx
		call_count = [0]

		def mock_stream(messages, tools=None, **kwargs):
			call_count[0] += 1
			if call_count[0] == 1:
				raise httpx.NetworkError("connection reset")
			async def gen():
				yield LLMStreamChunk(content_delta="ok")
			return gen()

		primary = MagicMock()
		primary.stream = mock_stream
		pm = PluginManager([])
		caller = LLMCaller(primary=primary, fallback=None, plugin_manager=pm)
		ctx = _make_ctx(stream=True)
		msg, events = await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
		assert msg["content"] == "ok"
		assert call_count[0] == 2

	@pytest.mark.asyncio
	async def test_stream_fallback_after_retry_fails(self):
		import httpx

		def fail_stream(messages, tools=None, **kwargs):
			raise httpx.TimeoutException("timeout")

		def fallback_stream(messages, tools=None, **kwargs):
			async def gen():
				yield LLMStreamChunk(content_delta="fallback")
			return gen()

		primary = MagicMock()
		primary.stream = fail_stream
		fallback = MagicMock()
		fallback.stream = fallback_stream
		pm = PluginManager([])
		caller = LLMCaller(primary=primary, fallback=fallback, plugin_manager=pm)
		ctx = _make_ctx(stream=True)
		msg, events = await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
		assert msg["content"] == "fallback"
		assert ctx.state.fallback_triggered is True

	@pytest.mark.asyncio
	async def test_stream_non_retryable_error_does_not_fallback(self):
		call_count = [0]

		def fail_stream(messages, tools=None, **kwargs):
			call_count[0] += 1
			raise ValueError("bad request")

		def fallback_stream(messages, tools=None, **kwargs):
			async def gen():
				yield LLMStreamChunk(content_delta="fb")
			return gen()

		primary = MagicMock()
		primary.stream = fail_stream
		fallback = MagicMock()
		fallback.stream = fallback_stream
		pm = PluginManager([])
		caller = LLMCaller(primary=primary, fallback=fallback, plugin_manager=pm)
		ctx = _make_ctx(stream=True)
		with pytest.raises(ValueError, match="bad request"):
			await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
		assert call_count[0] == 1

	@pytest.mark.asyncio
	async def test_stream_success_first_try(self):
		def ok_stream(messages, tools=None, **kwargs):
			async def gen():
				yield LLMStreamChunk(content_delta="hello")
			return gen()

		primary = MagicMock()
		primary.stream = ok_stream
		pm = PluginManager([])
		caller = LLMCaller(primary=primary, fallback=None, plugin_manager=pm)
		ctx = _make_ctx(stream=True)
		msg, events = await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
		assert msg["content"] == "hello"

	@pytest.mark.asyncio
	async def test_pre_llm_call_hook_applied(self):
		primary = MagicMock()
		primary.chat = AsyncMock(return_value=_make_response("ok"))
		pm = PluginManager([])
		caller = LLMCaller(primary=primary, fallback=None, plugin_manager=pm)
		ctx = _make_ctx(stream=False)
		await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
		assert primary.chat.called

	@pytest.mark.asyncio
	async def test_post_llm_call_receives_delta_and_total_usage(self):
		class UsagePlugin:
			name = "usage"
			def pre_llm_call(self, ctx, messages, tools):
				return messages, tools
			async def post_llm_call(self, ctx, messages, response, duration_ms):
				self.response = response

		primary = MagicMock()
		primary.chat = AsyncMock(return_value=_make_response("ok"))
		plugin = UsagePlugin()
		pm = PluginManager([plugin])
		caller = LLMCaller(primary=primary, fallback=None, plugin_manager=pm)
		ctx = _make_ctx(stream=False)
		ctx.add_usage(7, 2)
		await caller.call(ctx, [{"role": "user", "content": "hi"}], None)
		assert plugin.response["usage"] == {"input_tokens": 5, "output_tokens": 3}
		assert plugin.response["total_usage"] == {"input_tokens": 12, "output_tokens": 5}
