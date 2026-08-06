"""Black-box API tests backed by a real Engine and Agent runtime."""
import asyncio
import json

from fastapi.testclient import TestClient

from axc_agent_engine.api.app import create_app
from axc_agent_engine.core.schema import LLMStreamChunk, LLMUsage, ToolDefinition
from axc_agent_engine.engine import AgentModels, Engine
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.config_schema import config_schema
from axc_agent_engine.plugins.registry import PluginRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


class APIEchoPlugin(BasePlugin):
	name = "api_echo"
	config_schema = config_schema("api_echo", "API Echo", "API integration test tool.", [])
	invocations: list[dict] = []

	def get_tools(self):
		async def echo(arguments, context):
			type(self).invocations.append(dict(arguments))
			return ToolOutput.json_output({"echo": arguments["text"]})

		return [ToolDefinition(
			name="echo",
			description="Echo text",
			parameters={
				"type": "object",
				"properties": {"text": {"type": "string"}},
				"required": ["text"],
			},
			execute=echo,
		)]


class APISequenceProvider:
	model = "api-sequence"
	tool_name_mapping = None

	def __init__(self) -> None:
		self.requests: list[list[dict]] = []

	async def chat(self, messages, tools=None, **kwargs):
		raise AssertionError("API Agent should use the streaming provider path")

	async def stream(self, messages, tools=None, **kwargs):
		self.requests.append([dict(message) for message in messages])
		if not any(message.get("role") == "tool" for message in messages):
			yield LLMStreamChunk(
				tool_call_delta={
					"index": 0,
					"id": "api-tool-call",
					"function": {"name": "echo", "arguments": '{"text":"api"}'},
				},
				usage=LLMUsage(input_tokens=4, output_tokens=1),
			)
			return
		tool_message = next(message for message in reversed(messages) if message.get("role") == "tool")
		assert json.loads(tool_message["content"]) == {"echo": "api"}
		yield LLMStreamChunk(
			content_delta="API tool roundtrip complete",
			usage=LLMUsage(input_tokens=6, output_tokens=2),
		)

	async def ask(self, prompt, **kwargs):
		return ""

	async def close(self):
		pass


def _real_api(tmp_path):
	APIEchoPlugin.invocations = []
	(tmp_path / "api_agent.yaml").write_text(
		"""
name: api_agent
description: real API integration agent
system_prompt: Use the echo tool when requested.
runtime:
  max_rounds: 4
plugins:
  api_echo:
    enabled: true
""",
		encoding="utf-8",
	)
	registry = PluginRegistry()
	registry.register(APIEchoPlugin)
	engine = Engine(plugin_registry=registry)
	provider = APISequenceProvider()
	app = create_app(engine, models=AgentModels(default=provider), agents_dir=str(tmp_path))
	return engine, provider, TestClient(app)


def test_sync_api_runs_real_engine_agent_tool_roundtrip(tmp_path):
	engine, provider, client = _real_api(tmp_path)
	try:
		response = client.post("/v1/chat/completions", json={
			"model": "api_agent",
			"messages": [{"role": "user", "content": "echo api"}],
			"session_id": "api-sync-session",
		})
		assert response.status_code == 200
		body = response.json()
		assert body["choices"][0]["message"]["content"] == "API tool roundtrip complete"
		assert body["choices"][0]["finish_reason"] == "stop"
		assert body["usage"] == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
		assert APIEchoPlugin.invocations == [{"text": "api"}]
		assert [message["role"] for message in provider.requests[1]][-2:] == ["assistant", "tool"]
	finally:
		asyncio.run(engine.close())


def test_stream_api_runs_real_engine_and_finishes_after_internal_tool(tmp_path):
	engine, provider, client = _real_api(tmp_path)
	try:
		response = client.post("/v1/chat/completions", json={
			"model": "api_agent",
			"messages": [{"role": "user", "content": "echo api"}],
			"session_id": "api-stream-session",
			"stream": True,
			"stream_options": {"include_usage": True},
		})
		assert response.status_code == 200
		assert response.headers["content-type"].startswith("text/event-stream")
		data_lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
		assert data_lines[-1] == "[DONE]"
		chunks = [json.loads(line) for line in data_lines[:-1]]
		assert any(chunk.get("choices", [{}])[0].get("delta", {}).get("content") == "API tool roundtrip complete" for chunk in chunks if chunk.get("choices"))
		assert any("tool_calls" in chunk.get("choices", [{}])[0].get("delta", {}) for chunk in chunks if chunk.get("choices"))
		finish_reasons = [
			chunk["choices"][0]["finish_reason"]
			for chunk in chunks
			if chunk.get("choices") and chunk["choices"][0].get("finish_reason") is not None
		]
		assert finish_reasons == ["stop"]
		usage_chunk = next(chunk for chunk in chunks if not chunk.get("choices") and chunk.get("usage"))
		assert usage_chunk["usage"] == {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
		assert APIEchoPlugin.invocations == [{"text": "api"}]
		assert len(provider.requests) == 2
	finally:
		asyncio.run(engine.close())
