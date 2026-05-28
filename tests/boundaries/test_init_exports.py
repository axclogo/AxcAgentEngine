"""Tests for #33 __init__.py minimal public API."""
import importlib.util

import axc_agent_engine


class TestMinimalExports:
	def test_all_list_minimal(self):
		expected = {"Engine", "AgentModels", "AgentTemplate", "LLMConfig", "Agent", "Event", "EventType", "BasePlugin", "ToolDefinition", "tool",
					"ConcurrencyConfig", "ExecutionLimiter", "RateLimiter", "SessionExecutionGate",
				"LLMMessage", "LLMUsage", "LLMResponse", "LLMStreamChunk", "Capability",
				"ToolOutput", "ArtifactRef", "ResultStore",
				"AuditEvent", "AuditEventType", "InMemoryAuditSink", "ErrorEnvelope", "ErrorCategory",
				"Checkpoint", "CheckpointStatus", "CheckpointStore", "InMemoryCheckpointStore",
			"CapabilityPolicyEvaluator", "PolicyDecision", "PolicyEvaluator", "PolicyRequest",
			"InputProviderResult", "InputProvider", "PassthroughInputProvider",
			"ResourceRegistry", "ResourceError", "ResourceNotFoundError",
			"ResourceTypeError", "DuplicateResourceError",
			"PluginRegistry"}
		assert set(axc_agent_engine.__all__) == expected

	def test_no_tool_result_in_top_level(self):
		assert not hasattr(axc_agent_engine, 'ToolResult')

	def test_no_multi_agent_session_in_top_level(self):
		assert not hasattr(axc_agent_engine, 'MultiAgentSession')

	def test_no_openai_client_in_top_level(self):
		assert not hasattr(axc_agent_engine, 'OpenAIClient')

	def test_no_eval_runner_in_top_level(self):
		assert not hasattr(axc_agent_engine, 'EvalRunner')

	def test_no_shared_context_in_top_level(self):
		assert not hasattr(axc_agent_engine, 'SharedContext')

	def test_no_step_status_in_top_level(self):
		assert not hasattr(axc_agent_engine, 'StepStatus')

	def test_no_session_mode_in_top_level(self):
		assert not hasattr(axc_agent_engine, 'SessionMode')

	def test_no_usage_in_top_level(self):
		assert not hasattr(axc_agent_engine, 'Usage')

	def test_no_sandbox_types_in_top_level(self):
		assert not hasattr(axc_agent_engine, 'CommandSpec')
		assert not hasattr(axc_agent_engine, 'LocalSubprocessExecutor')

	def test_no_sandbox_facade(self):
		assert importlib.util.find_spec("axc_agent_engine.runtime.sandbox") is None

	def test_engine_importable(self):
		assert axc_agent_engine.Engine is not None

	def test_agent_importable(self):
		assert axc_agent_engine.Agent is not None

	def test_event_importable(self):
		assert axc_agent_engine.Event is not None

	def test_base_plugin_importable(self):
		assert axc_agent_engine.BasePlugin is not None

	def test_tool_definition_importable(self):
		assert axc_agent_engine.ToolDefinition is not None

	def test_tool_decorator_importable(self):
		assert callable(axc_agent_engine.tool)

	def test_version_string(self):
		assert isinstance(axc_agent_engine.__version__, str)
		assert len(axc_agent_engine.__version__) > 0
