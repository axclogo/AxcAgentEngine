"""Tests for schema module — data models, enums, configs."""
import pytest
from pydantic import ValidationError

from axc_agent_engine.core.schema import (
	StepStatus, RiskLevel, PluginSignal,
	ToolDefinition, LLMUsage, RuntimeConfig, PluginConfig, AgentConfig, ConcurrencyRuntimeConfig,
)


class TestEnums:
	def test_step_status(self):
		assert StepStatus.PENDING == "pending"
		assert StepStatus.DONE == "done"
		assert StepStatus.FAILED == "failed"

	def test_risk_level(self):
		assert RiskLevel.SAFE == "safe"
		assert RiskLevel.BLOCKED == "blocked"

	def test_plugin_signal(self):
		assert PluginSignal.NONE == "none"
		assert PluginSignal.STOP == "stop"
		assert PluginSignal.WARN == "warn"
		assert PluginSignal.SKIP == "skip"

class TestToolDefinition:
	def test_basic(self):
		td = ToolDefinition(name="test", description="desc")
		assert td.name == "test"
		assert td.is_read_only is False
		assert td.timeout == 120
		assert td.deferred is False

	def test_openai_schema(self):
		td = ToolDefinition(name="echo", description="Echo text", parameters={
			"type": "object", "properties": {"text": {"type": "string"}}
		})
		schema = td.to_openai_schema()
		assert schema["type"] == "function"
		assert schema["function"]["name"] == "echo"
		assert schema["function"]["description"] == "Echo text"

	def test_defaults(self):
		td = ToolDefinition(name="x")
		assert td.parameters == {"type": "object", "properties": {}}
		assert td.execute is None


class TestUsage:
	def test_defaults(self):
		u = LLMUsage()
		assert u.input_tokens == 0
		assert u.output_tokens == 0

	def test_custom(self):
		u = LLMUsage(input_tokens=100, output_tokens=50)
		assert u.input_tokens == 100


class TestRuntimeConfig:
	def test_defaults(self):
		rc = RuntimeConfig()
		assert rc.max_rounds == 50
		assert rc.thinking == "auto"
		assert rc.parallel_tool_calls is True
		assert rc.human_in_the_loop is False
		assert rc.concurrency.max_session_concurrent_runs == 1

	def test_validation(self):
		with pytest.raises(ValidationError):
			RuntimeConfig(max_rounds=0)  # ge=1
		with pytest.raises(ValidationError):
			RuntimeConfig(thinking="invalid")  # pattern mismatch
		with pytest.raises(ValidationError):
			ConcurrencyRuntimeConfig(max_agent_concurrent_runs=-1)

	def test_extra_forbid(self):
		with pytest.raises(ValidationError):
			RuntimeConfig(unknown_field="x")


class TestPluginConfig:
	def test_defaults(self):
		pc = PluginConfig()
		assert pc.enabled is False

	def test_extra_allowed(self):
		pc = PluginConfig(enabled=True, custom_field="value")
		assert pc.enabled is True


class TestAgentConfig:
	def test_minimal(self):
		ac = AgentConfig(name="test")
		assert ac.name == "test"
		assert ac.description == ""
		assert ac.system_prompt == ""

	def test_full(self):
		ac = AgentConfig(
			name="agent1",
			description="Test agent",
			system_prompt="You are helpful",
			runtime=RuntimeConfig(max_rounds=20),
			plugins={"compress": PluginConfig(enabled=True)},
		)
		assert ac.runtime.max_rounds == 20
		assert ac.plugins["compress"].enabled is True

	def test_extra_forbid(self):
		with pytest.raises(ValidationError):
			AgentConfig(name="test", unknown="x")
