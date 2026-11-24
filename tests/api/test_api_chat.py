"""Tests for OpenAI Chat Completions API parity."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from axc_agent_engine.api.app import create_app
from axc_agent_engine.api.deps import EngineState
from axc_agent_engine.api.routes.chat import (
	_CHAT_OPTIONS,
	_CHAT_VALIDATOR,
	ChatMessage,
	ChatRequest,
	_capabilities,
	_error_response,
	_stream_response,
	_sync_response,
	create_chat_router,
)
from axc_agent_engine.core.events import Event


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
			temperature=0.7,
			max_tokens=200,
			top_p=0.9,
			stop=["END"],
			presence_penalty=0.1,
			frequency_penalty=0.2,
			seed=42,
			user="test_user",
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
		resp = self._client().post(
			"/v1/chat/completions",
			json={
				"model": "agent",
				"messages": [{"role": "user", "content": "hi"}],
				"n": 2,
			},
		)
		assert resp.status_code == 400
		assert resp.json()["error"]["code"] == "unsupported_parameter"

	def test_rejects_request_level_tools(self):
		resp = self._client().post(
			"/v1/chat/completions",
			json={
				"model": "agent",
				"messages": [{"role": "user", "content": "hi"}],
				"tools": [{"type": "function", "function": {"name": "x", "parameters": {}}}],
			},
		)
		assert resp.status_code == 400
		assert resp.json()["error"]["code"] == "unsupported_parameter"

	def test_rejects_tool_choice(self):
		resp = self._client().post(
			"/v1/chat/completions",
			json={
				"model": "agent",
				"messages": [{"role": "user", "content": "hi"}],
				"tool_choice": "auto",
			},
		)
		assert resp.status_code == 400
		assert resp.json()["error"]["code"] == "unsupported_parameter"

	def test_rejects_unknown_parameter(self):
		resp = self._client().post(
			"/v1/chat/completions",
			json={
				"model": "agent",
				"messages": [{"role": "user", "content": "hi"}],
				"logprobs": True,
			},
		)
		assert resp.status_code == 400
		assert resp.json()["error"]["param"] == "logprobs"

	def test_capabilities_route(self):
		resp = self._client().get("/v1/capabilities")
		assert resp.status_code == 200
		body = resp.json()
		assert body["openai_compatibility"]["api"] == "chat_completions"
		assert "/v1/capabilities" in body["openai_compatibility"]["routes"]

	def test_missing_agent_or_model_is_rejected(self):
		resp = self._client().post(
			"/v1/chat/completions",
			json={
				"messages": [{"role": "user", "content": "hi"}],
			},
		)
		assert resp.status_code == 400


class FakeAPIAgent:
	description = "fake"

	async def stream_with_messages(self, messages, session_id="", llm_options=None):
		yield Event.cost_update(3, 2)
		yield Event.delta("he")
		yield Event.tool_call("lookup", "tc1", {"q": "x"})
		yield Event.tool_args_preview("lookup", "tc1", '{"q"', '{"q"', 0)
		yield Event.done("hello")


async def test_sync_response_collects_done_and_usage():
	request = ChatRequest(model="agent", messages=[ChatMessage(role="user", content="hi")])
	body = await _sync_response(FakeAPIAgent(), [{"role": "user", "content": "hi"}], request, {"temperature": 0.2})
	assert body["choices"][0]["message"]["content"] == "hello"
	assert body["usage"]["total_tokens"] == 5


async def test_sync_response_returns_error_response():
	class ErrorAgent:
		async def stream_with_messages(self, messages, session_id="", llm_options=None):
			yield Event.error("bad")

	request = ChatRequest(model="agent", messages=[ChatMessage(role="user", content="hi")])
	resp = await _sync_response(ErrorAgent(), [{"role": "user", "content": "hi"}], request, {})
	assert resp.status_code == 500


async def test_stream_response_emits_content_tool_usage_and_done():
	request = ChatRequest(
		model="agent",
		stream=True,
		stream_options={"include_usage": True},
		messages=[ChatMessage(role="user", content="hi")],
	)
	chunks = [
		chunk async for chunk in _stream_response(FakeAPIAgent(), [{"role": "user", "content": "hi"}], request, {})
	]
	joined = "".join(chunks)
	assert '"role": "assistant"' in joined
	assert '"content": "he"' in joined
	assert '"tool_calls"' in joined
	assert '"usage"' in joined
	assert chunks[-1] == "data: [DONE]\n\n"


async def test_engine_state_uses_host_registry_and_directory(tmp_path, mock_llm):
	from axc_agent_engine import AgentModels, Engine

	root = tmp_path / "templates"
	root.mkdir()
	allowed = root / "first.yaml"
	allowed.write_text("name: configured\ndescription: local")
	outside = tmp_path / "external.yaml"
	outside.write_text("name: other")
	(root / "external-link.yaml").symlink_to(outside)
	(root / "valid-link.yml").symlink_to(allowed)
	(root / "directory.yml").mkdir()
	engine = Engine()
	state = EngineState(engine=engine, models=AgentModels(default=mock_llm), agents_dir=str(root))
	try:
		preloaded = engine.load_agent_template(str(allowed)).instantiate(models=state.models)
		preloaded.name = "public label"
		assert state.get_agent("configured") is preloaded
		assert state.get_agent("first") is preloaded
		assert state.get_agent("valid-link") is preloaded
		assert sorted(a["name"] for a in state.list_agents()) == ["configured", "first", "valid-link"]
		for name in ("missing", "first.yaml", str(outside), "../external", "external-link", "directory"):
			assert state.get_agent(name) is None
		await engine.unload_agent("configured")
		assert state.get_agent("configured") is None
		assert state.get_agent("first") is not preloaded
		assert len(engine.list_agents()) == 1
	finally:
		await engine.close()


def test_create_app_routes_and_error_handler(tmp_path, mock_llm):
	from axc_agent_engine import AgentModels, Engine
	from axc_agent_engine.core.errors import ProviderError

	agent_file = tmp_path / "agent.yaml"
	agent_file.write_text("name: agent\n", encoding="utf-8")
	app = create_app(Engine(), models=AgentModels(default=mock_llm), agents_dir=str(tmp_path))
	client = TestClient(app)
	assert client.get("/health").json() == {"status": "ok"}
	assert client.get("/ready").json() == {"status": "ready"}
	resp = client.post(
		"/v1/chat/completions",
		json={
			"model": "agent",
			"messages": [{"role": "user", "content": "hi"}],
		},
	)
	assert resp.status_code == 200

	@app.get("/boom")
	async def boom():
		raise ProviderError("provider down")

	assert client.get("/boom").status_code == 502


def test_http_por_stream_and_completion_agree_on_final_answer_and_usage(tmp_path):
	import json

	from axc_agent_engine import AgentModels, Engine, LLMMessage, LLMResponse, LLMStreamChunk, LLMUsage, PluginRegistry
	from axc_agent_engine.plugins.builtin import PORPlugin

	class Model:
		model = "offline"

		async def chat(self, messages, tools=None, **kwargs):
			plan = {"goal": "finish", "steps": [{"step_id": 1, "description": "read"}]}
			return LLMResponse(message=LLMMessage(content=json.dumps(plan)))

		async def stream(self, messages, tools=None, **kwargs):
			yield LLMStreamChunk(content_delta="answer", usage=LLMUsage(input_tokens=3, output_tokens=2))

		async def ask(self, prompt, **kwargs):
			return '{"action":"done","step_ok":true,"goal_achieved":true}'

	registry = PluginRegistry()
	registry.register(PORPlugin)
	engine = Engine(plugin_registry=registry)
	models = AgentModels(default=Model())
	(tmp_path / "report.yaml").write_text("name: report\nplugins: {por: {enabled: true}}")
	app = create_app(engine, models=models, agents_dir=str(tmp_path))
	request = {"model": "report", "messages": [{"role": "user", "content": "prepare report"}]}
	with TestClient(app) as client:
		buffered = client.post("/v1/chat/completions", json=request)
		assert buffered.status_code == 200
		stream = client.post(
			"/v1/chat/completions", json={**request, "stream": True, "stream_options": {"include_usage": True}}
		)
		assert stream.status_code == 200
	lines = [line.removeprefix("data: ") for line in stream.text.splitlines() if line]
	assert lines[-1] == "[DONE]"
	chunks = [json.loads(line) for line in lines[:-1]]
	text = "".join(c["delta"].get("content", "") for chunk in chunks for c in chunk["choices"])
	assert text == buffered.json()["choices"][0]["message"]["content"] == "answer"
	assert (
		chunks[-1]["usage"]
		== buffered.json()["usage"]
		== {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10}
	)


async def test_http_terminal_text_completes_deltas_once():
	import json

	class Agent:
		def __init__(self, deltas):
			self.deltas = deltas

		async def stream_with_messages(self, *args, **kwargs):
			for delta in self.deltas:
				yield Event.delta(delta)
			yield Event.done("hello")

	request = ChatRequest(model="demo", messages=[ChatMessage(role="user", content="hi")])
	for deltas in [[], ["he"], ["he", "llo"]]:
		chunks = [
			json.loads(chunk.removeprefix("data: "))
			async for chunk in _stream_response(Agent(deltas), [], request, {})
			if "[DONE]" not in chunk
		]
		assert "".join(choice["delta"].get("content", "") for chunk in chunks for choice in chunk["choices"]) == "hello"


async def test_http_cancellation_returns_reason_and_partial_stream_usage():
	import json

	class Agent:
		async def stream_with_messages(self, *args, **kwargs):
			yield Event.cancelled("host stopped", {"usage": {"input_tokens": 3, "output_tokens": 1}})

	req = ChatRequest(
		model="demo", messages=[ChatMessage(role="user", content="hi")], stream_options={"include_usage": True}
	)
	response = await _sync_response(Agent(), [], req, {})
	assert response.status_code == 499
	assert json.loads(response.body)["error"]["code"] == "run_cancelled"

	chunks = [chunk async for chunk in _stream_response(Agent(), [], req, {})]
	assert chunks[-1] == "data: [DONE]\n\n"
	error, usage = [json.loads(chunk.removeprefix("data: ")) for chunk in chunks[-3:-1]]
	assert error["axc_event"] == {"type": "cancelled", "data": {"content": "host stopped"}}
	assert error["choices"][0]["finish_reason"] == "error"
	assert usage["usage"]["total_tokens"] == 4
