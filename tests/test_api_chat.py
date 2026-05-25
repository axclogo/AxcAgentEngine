"""Tests for OpenAI Chat Completions API parity."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from axc_agent_engine.api.routes.chat import (
	ChatMessage,
	ChatRequest,
	_CHAT_OPTIONS,
	_CHAT_VALIDATOR,
	_capabilities,
	_error_response,
	create_chat_router,
)


class TestChatRequest:
	def test_content_str(self):
		msg = ChatMessage(role="user", content="hello")
		assert msg.content == "hello"

	def test_content_parts_list(self):
		parts = [{"type": "text", "text": "hello"}, {"type": "image_url", "image_url": {"url": "http://img"}}]
		msg = ChatMessage(role="user", content=parts)
		assert isinstance(msg.content, list)
		assert len(msg.content) == 2

	def test_option_resolver_filters_none(self):
		req = ChatRequest(messages=[ChatMessage(role="user", content="hi")], temperature=0.5, max_tokens=100)
		opts = _CHAT_OPTIONS.llm_options(req)
		assert opts == {"temperature": 0.5, "max_tokens": 100}
		assert "top_p" not in opts
		assert "stop" not in opts

	def test_option_resolver_all_fields(self):
		req = ChatRequest(
			messages=[ChatMessage(role="user", content="hi")],
			temperature=0.7, max_tokens=200, top_p=0.9,
			stop=["END"], presence_penalty=0.1, frequency_penalty=0.2,
			seed=42, user="test_user",
			response_format={"type": "json_object"},
		)
		opts = _CHAT_OPTIONS.llm_options(req)
		assert opts["temperature"] == 0.7
		assert opts["stop"] == ["END"]
		assert opts["seed"] == 42
		assert opts["response_format"] == {"type": "json_object"}

	def test_n_greater_than_1_field(self):
		req = ChatRequest(messages=[ChatMessage(role="user", content="hi")], n=2)
		assert req.n == 2

	def test_tools_field(self):
		req = ChatRequest(
			messages=[ChatMessage(role="user", content="hi")],
			tools=[{"type": "function", "function": {"name": "test", "parameters": {}}}],
		)
		assert len(req.tools) == 1

	def test_error_response_format(self):
		resp = _error_response(400, "bad request", "invalid_request_error", "bad_param")
		assert resp.status_code == 400
		import json
		body = json.loads(resp.body)
		assert body["error"]["message"] == "bad request"
		assert body["error"]["type"] == "invalid_request_error"
		assert body["error"]["code"] == "bad_param"
		assert body["error"]["param"] is None

	def test_rejects_unknown_openai_parameter(self):
		req = ChatRequest(messages=[ChatMessage(role="user", content="hi")], model="agent", logprobs=True)
		resp = _CHAT_VALIDATOR.validate(req)
		assert resp.status_code == 400
		import json
		body = json.loads(resp.body)
		assert body["error"]["param"] == "logprobs"
		assert body["error"]["code"] == "unsupported_parameter"

	def test_validator_accepts_stream_include_usage_option(self):
		req = ChatRequest(
			messages=[ChatMessage(role="user", content="hi")],
			model="agent",
			stream=True,
			stream_options={"include_usage": True},
		)
		assert _CHAT_VALIDATOR.validate(req) is None

	def test_rejects_stream_options_without_stream(self):
		req = ChatRequest(
			messages=[ChatMessage(role="user", content="hi")],
			model="agent",
			stream_options={"include_usage": True},
		)
		resp = _CHAT_VALIDATOR.validate(req)
		assert resp.status_code == 400

	def test_capabilities_declares_subset(self):
		caps = _capabilities()
		assert caps["openai_compatibility"]["level"] == "subset"
		assert caps["chat_completions"]["request_level_tools"] is False
		assert "stream_options" in caps["chat_completions"]["supported_parameters"]


class TestMultiTurnMessages:
	def test_messages_preserved_in_model_dump(self):
		"""Multi-turn messages should not lose system/assistant history."""
		messages = [
			ChatMessage(role="system", content="You are helpful"),
			ChatMessage(role="user", content="What is 2+2?"),
			ChatMessage(role="assistant", content="4"),
			ChatMessage(role="user", content="And 3+3?"),
		]
		dumped = [m.model_dump() for m in messages]
		assert len(dumped) == 4
		assert dumped[0]["role"] == "system"
		assert dumped[2]["role"] == "assistant"
		assert dumped[3]["content"] == "And 3+3?"


class TestUnsupportedRequestParameters:
	def _client(self) -> TestClient:
		class State:
			def get_agent(self, agent_name: str):
				raise AssertionError("unsupported parameter validation should run before agent lookup")

			def list_agents(self):
				return []

		app = FastAPI()
		app.include_router(create_chat_router(State()))
		return TestClient(app)

	def test_rejects_n_greater_than_one(self):
		resp = self._client().post("/v1/chat/completions", json={
			"model": "agent",
			"messages": [{"role": "user", "content": "hi"}],
			"n": 2,
		})
		assert resp.status_code == 400
		assert resp.json()["error"]["code"] == "unsupported_parameter"

	def test_rejects_request_level_tools(self):
		resp = self._client().post("/v1/chat/completions", json={
			"model": "agent",
			"messages": [{"role": "user", "content": "hi"}],
			"tools": [{"type": "function", "function": {"name": "x", "parameters": {}}}],
		})
		assert resp.status_code == 400
		assert resp.json()["error"]["code"] == "unsupported_parameter"

	def test_rejects_tool_choice(self):
		resp = self._client().post("/v1/chat/completions", json={
			"model": "agent",
			"messages": [{"role": "user", "content": "hi"}],
			"tool_choice": "auto",
		})
		assert resp.status_code == 400
		assert resp.json()["error"]["code"] == "unsupported_parameter"

	def test_rejects_unknown_parameter(self):
		resp = self._client().post("/v1/chat/completions", json={
			"model": "agent",
			"messages": [{"role": "user", "content": "hi"}],
			"logprobs": True,
		})
		assert resp.status_code == 400
		assert resp.json()["error"]["param"] == "logprobs"

	def test_capabilities_route(self):
		resp = self._client().get("/v1/capabilities")
		assert resp.status_code == 200
		body = resp.json()
		assert body["openai_compatibility"]["api"] == "chat_completions"
		assert "/v1/capabilities" in body["openai_compatibility"]["routes"]
