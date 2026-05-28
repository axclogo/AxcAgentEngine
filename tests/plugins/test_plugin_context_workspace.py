"""Tests for #24 PluginContext workspace field."""
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins import agent_info_from_runtime, model_info_from_models


class TestPluginContextWorkspace:
	def test_default_workspace_empty(self):
		ctx = PluginContext()
		assert ctx.workspace == ""

	def test_workspace_set(self):
		ctx = PluginContext(workspace="/project/root")
		assert ctx.workspace == "/project/root"

	def test_workspace_with_other_fields(self):
		from unittest.mock import MagicMock
		llm = MagicMock()
		ctx = PluginContext(default_model=llm, workspace="/ws")
		assert ctx.workspace == "/ws"
		assert ctx.default_model is llm

	def test_get_agent_without_getter(self):
		ctx = PluginContext()
		assert ctx.get_agent("test") is None

	def test_list_agents_without_lister(self):
		ctx = PluginContext()
		assert ctx.list_agents() == []

	def test_get_agent_with_getter(self):
		agent = object()
		ctx = PluginContext(agent_getter=lambda name: agent if name == "x" else None)
		assert ctx.get_agent("x") is agent
		assert ctx.get_agent("y") is None

	def test_model_info_from_models(self):
		class Provider:
			model = "gpt-test"
		ctx = PluginContext(model_info=model_info_from_models(Provider()))
		assert ctx.model_name == "gpt-test"
		assert ctx.model_info.to_dict()["active"] == "gpt-test"

	def test_agent_info_from_runtime(self):
		ctx = PluginContext(agent_info=agent_info_from_runtime(
			name="agent-a",
			description="desc",
			workspace="/ws",
			routing_mode="react_only",
		))
		assert ctx.agent_name == "agent-a"
		assert ctx.agent_info.to_dict() == {
			"name": "agent-a",
			"description": "desc",
			"workspace": "/ws",
			"session_id": "",
			"routing_mode": "react_only",
		}
