"""Tests for ExecutionConfig workspace field addition."""
import pytest
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext


class TestExecutionConfigWorkspace:
	def test_default_workspace_empty(self):
		config = ExecutionConfig()
		assert config.workspace == ""

	def test_workspace_set(self):
		config = ExecutionConfig(workspace="/my/project")
		assert config.workspace == "/my/project"

	def test_config_is_frozen(self):
		config = ExecutionConfig(workspace="/ws")
		with pytest.raises(Exception):
			config.workspace = "/other"

	def test_context_with_workspace(self):
		ctx = ExecutionContext(config=ExecutionConfig(workspace="/ws"))
		assert ctx.config.workspace == "/ws"

	def test_all_config_defaults(self):
		config = ExecutionConfig()
		assert config.system_prompt == ""
		assert config.max_rounds == 50
		assert config.stream is False
		assert config.thinking == "auto"
		assert config.parallel_tool_calls is True
		assert config.human_in_the_loop is False
		assert config.stream_idle_timeout == 60
		assert config.step_timeout == 300
		assert config.total_timeout == 600
		assert config.workspace == ""
