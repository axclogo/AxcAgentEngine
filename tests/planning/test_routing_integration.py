"""End-to-end routing tests through Engine, Agent, ReAct, and POR."""
import json

from axc_agent_engine.core.events import EventType
from axc_agent_engine.core.schema import LLMStreamChunk, LLMUsage, ToolDefinition
from axc_agent_engine.engine import AgentModels, Engine
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.config_schema import config_schema
from axc_agent_engine.plugins.registry import PluginRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


PLAN_CONTENT = json.dumps({
	"goal": "complete routed task",
	"steps": [{
		"step_id": 1,
		"description": "execute the routed step",
		"depends_on": [],
		"tools_needed": [],
	}],
})


class RoutingProvider:
	model = "routing-sequence"
	tool_name_mapping = None

	def __init__(self, responses: list[dict]) -> None:
		self.responses = list(responses)
		self.requests: list[list[dict]] = []

	async def chat(self, messages, tools=None, **kwargs):
		raise AssertionError("routing integration tests use Agent.stream")

	async def stream(self, messages, tools=None, **kwargs):
		self.requests.append([dict(message) for message in messages])
		response = self.responses.pop(0)
		yield LLMStreamChunk(
			content_delta=response.get("content", ""),
			tool_call_delta=response.get("tool_call"),
			usage=LLMUsage(input_tokens=1, output_tokens=1),
		)

	async def ask(self, prompt, **kwargs):
		return json.dumps({"action": "done", "step_ok": True, "goal_achieved": True})

	async def close(self):
		pass


class RoutingToolPlugin(BasePlugin):
	name = "routing_tool"
	config_schema = config_schema("routing_tool", "Routing Tool", "Routing precedence test tool.", [])
	invocations: list[dict] = []

	def get_tools(self):
		async def echo(arguments, context):
			type(self).invocations.append(dict(arguments))
			return ToolOutput.text(f"echo:{arguments['value']}")

		return [ToolDefinition(
			name="routing_echo",
			parameters={
				"type": "object",
				"properties": {"value": {"type": "string"}},
				"required": ["value"],
			},
			execute=echo,
		)]


def _agent(tmp_path, provider, routing_mode: str, with_tool: bool = False):
	plugin_yaml = ""
	registry = PluginRegistry()
	if with_tool:
		RoutingToolPlugin.invocations = []
		registry.register(RoutingToolPlugin)
		plugin_yaml = """
plugins:
  routing_tool:
    enabled: true
"""
	path = tmp_path / f"routing_{routing_mode}.yaml"
	path.write_text(
		f"""
name: routing_{routing_mode}
system_prompt: Route deterministically.
runtime:
  max_rounds: 8
  routing:
    mode: {routing_mode}
{plugin_yaml}
""",
		encoding="utf-8",
	)
	engine = Engine(plugin_registry=registry)
	agent = engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=provider))
	return engine, agent


async def test_auto_mode_hands_structured_plan_to_real_por_runtime(tmp_path):
	provider = RoutingProvider([
		{"content": PLAN_CONTENT},
		{"content": "routed step result"},
		{"content": "routed final summary"},
	])
	engine, agent = _agent(tmp_path, provider, "auto")
	try:
		events = [event async for event in agent.stream("route this task", session_id="por-auto")]
	finally:
		await engine.close()

	types = [event.type for event in events]
	assert types.index(EventType.PLAN_CREATED) < types.index(EventType.STEP_START)
	assert types.index(EventType.STEP_START) < types.index(EventType.STEP_COMPLETED) < types.index(EventType.DONE)
	assert events[-1].content == "routed final summary"
	assert len(provider.requests) == 3
	assert any("当前步骤 1" in str(message.get("content", "")) for message in provider.requests[1])


async def test_react_only_treats_structured_plan_as_final_answer(tmp_path):
	provider = RoutingProvider([{"content": PLAN_CONTENT}])
	engine, agent = _agent(tmp_path, provider, "react_only")
	try:
		events = [event async for event in agent.stream("do not enter por")]
	finally:
		await engine.close()

	assert not any(event.type in {EventType.PLAN_CREATED, EventType.STEP_START, EventType.STEP_COMPLETED} for event in events)
	assert events[-1].type == EventType.DONE
	assert events[-1].content == PLAN_CONTENT
	assert len(provider.requests) == 1


async def test_tool_call_takes_precedence_over_plan_like_content(tmp_path):
	provider = RoutingProvider([
		{
			"content": PLAN_CONTENT,
			"tool_call": {
				"index": 0,
				"id": "mixed-tool-call",
				"function": {"name": "routing_echo", "arguments": '{"value":"mixed"}'},
			},
		},
		{"content": "react continued after tool"},
	])
	engine, agent = _agent(tmp_path, provider, "auto", with_tool=True)
	try:
		events = [event async for event in agent.stream("use the requested tool")]
	finally:
		await engine.close()

	assert RoutingToolPlugin.invocations == [{"value": "mixed"}]
	assert any(event.type == EventType.TOOL_CALL for event in events)
	assert any(event.type == EventType.TOOL_RESULT and event.content == "echo:mixed" for event in events)
	assert not any(event.type == EventType.PLAN_CREATED for event in events)
	assert events[-1].type == EventType.DONE
	assert events[-1].content == "react continued after tool"
	assert provider.requests[1][-1]["role"] == "tool"
