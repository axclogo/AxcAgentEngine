"""Tests for LLM provider protocol and client."""
import httpx
import pytest

from axc_agent_engine.llm.provider import LLMProvider, EmbeddingProvider
from axc_agent_engine.llm.client import OpenAIClient, OpenAIErrorMapper
from axc_agent_engine.llm.config import LLMConfig
from axc_agent_engine.core.errors import ProviderBadRequestError, RetryableProviderError
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMStreamChunk
from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig


class TestLLMProviderProtocol:
	def test_protocol_check(self):
		class MyProvider:
			@property
			def model(self): return "test"
			@property
			def tool_name_mapping(self): return None
			async def chat(self, messages, tools=None, **kwargs):
				return LLMResponse(message=LLMMessage(content="ok"))
			async def stream(self, messages, tools=None, **kwargs):
				yield LLMStreamChunk(content_delta="ok")
			async def ask(self, prompt, **kwargs): return ""
			async def close(self): pass

		assert isinstance(MyProvider(), LLMProvider)

	def test_non_conforming(self):
		class Bad:
			pass
		assert not isinstance(Bad(), LLMProvider)


class TestEmbeddingProviderProtocol:
	def test_protocol_check(self):
		class MyEmbedder:
			async def embed(self, texts): return [[0.1, 0.2]]
			async def close(self): pass

		assert isinstance(MyEmbedder(), EmbeddingProvider)


class TestOpenAIClient:
	def test_init(self):
		config = LLMConfig(base_url="http://localhost:8080", api_key="key", model="gpt-4")
		client = OpenAIClient(config)
		assert client.model == "gpt-4"

	def test_build_payload(self):
		config = LLMConfig(base_url="http://localhost:8080", api_key="key", model="gpt-4", temperature=0.5)
		client = OpenAIClient(config)
		payload = client._build_payload([{"role": "user", "content": "hi"}])
		assert payload["model"] == "gpt-4"
		assert payload["temperature"] == 0.5
		assert payload["messages"][0]["content"] == "hi"

	def test_build_payload_with_tools(self):
		config = LLMConfig(base_url="http://localhost:8080", api_key="key", model="gpt-4")
		client = OpenAIClient(config)
		tools = [{"type": "function", "function": {"name": "test"}}]
		payload = client._build_payload([{"role": "user", "content": "hi"}], tools, parallel_tool_calls=True)
		assert payload["tools"] == tools
		assert payload["parallel_tool_calls"] is True

	def test_tool_name_mapping_config_exposed(self):
		config = LLMConfig(base_url="http://localhost:8080", api_key="key", model="gpt-4")
		client = OpenAIClient(config)
		assert isinstance(client.tool_name_mapping, ToolNameMappingConfig)

	def test_build_payload_with_thinking(self):
		config = LLMConfig(base_url="http://localhost:8080", api_key="key", model="gpt-4")
		client = OpenAIClient(config)
		payload = client._build_payload([], thinking="always", thinking_budget=5000)
		assert payload["thinking"]["type"] == "always"
		assert payload["thinking"]["budget_tokens"] == 5000

	@pytest.mark.asyncio
	async def test_close(self):
		config = LLMConfig(base_url="http://localhost:8080", api_key="key", model="gpt-4")
		client = OpenAIClient(config)
		await client.close()
		# Should not raise even if no client was created

	@pytest.mark.asyncio
	async def test_chat_uses_fake_httpx_client(self):
		class FakeResponse:
			def raise_for_status(self):
				return None

			def json(self):
				return {
					"choices": [{"message": {"role": "assistant", "content": "ok"}}],
					"usage": {"prompt_tokens": 2, "completion_tokens": 3},
				}

		class FakeClient:
			is_closed = False

			async def post(self, path, json):
				self.path = path
				self.payload = json
				return FakeResponse()

		config = LLMConfig(base_url="http://localhost:8080", api_key="key", model="gpt-4")
		client = OpenAIClient(config)
		fake = FakeClient()
		client._client = fake
		response = await client.chat([{"role": "user", "content": "hi"}])
		assert fake.path == "/chat/completions"
		assert fake.payload["messages"][0]["content"] == "hi"
		assert response.message.content == "ok"
		assert response.usage.input_tokens == 2

	@pytest.mark.asyncio
	async def test_stream_uses_fake_httpx_client(self):
		class FakeStreamResponse:
			def raise_for_status(self):
				return None

			async def __aenter__(self):
				return self

			async def __aexit__(self, exc_type, exc, tb):
				return False

			async def aiter_lines(self):
				yield 'data: {"choices":[{"delta":{"content":"he"},"finish_reason":null}]}'
				yield 'data: {"choices":[{"delta":{"content":"llo"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2}}'
				yield "data: [DONE]"

		class FakeClient:
			is_closed = False

			def stream(self, method, path, json):
				self.method = method
				self.path = path
				self.payload = json
				return FakeStreamResponse()

		config = LLMConfig(base_url="http://localhost:8080", api_key="key", model="gpt-4")
		client = OpenAIClient(config)
		fake = FakeClient()
		client._client = fake
		chunks = [chunk async for chunk in client.stream([{"role": "user", "content": "hi"}])]
		assert fake.method == "POST"
		assert fake.path == "/chat/completions"
		assert fake.payload["stream"] is True
		assert [c.content_delta for c in chunks] == ["he", "llo"]
		assert chunks[-1].usage.output_tokens == 2

	def test_provider_error_mapping(self):
		request = httpx.Request("POST", "http://test/chat/completions")
		bad_response = httpx.Response(400, request=request, text="bad")
		retry_response = httpx.Response(500, request=request, text="down")
		mapper = OpenAIErrorMapper()
		assert isinstance(mapper.provider_error_from_status(
			httpx.HTTPStatusError("bad", request=request, response=bad_response),
			"LLM returned error",
		), ProviderBadRequestError)
		assert isinstance(mapper.provider_error_from_status(
			httpx.HTTPStatusError("down", request=request, response=retry_response),
			"LLM returned error",
		), RetryableProviderError)


class TestLLMConfig:
	def test_defaults(self):
		config = LLMConfig(base_url="http://x", api_key="k", model="m")
		assert config.temperature == 0.7
		assert config.max_tokens is None
		assert config.timeout == 120
		assert config.max_concurrent_requests == 0
		assert config.requests_per_minute == 0
