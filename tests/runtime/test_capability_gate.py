"""Tests for capability-based tool permission gating."""
import pytest
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionState
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.schema import ToolDefinition, Capability
from axc_agent_engine.tools.orchestrator import execute_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


class TestCapabilityGate:
	@pytest.mark.asyncio
	async def test_tool_rejected_when_capability_not_allowed(self):
		"""Tool with capability not in allowed_capabilities is rejected."""
		async def fake_shell(args, ctx):
			return ToolOutput.text("should not run")
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="shell", execute=fake_shell, capability=Capability.SHELL))
		ctx = ExecutionContext(
			config=ExecutionConfig(allowed_capabilities=frozenset({Capability.FILE_READ})),
			state=ExecutionState(),
		)
		pm = PluginManager([])
		results = await execute_tool_calls(
			[{"name": "shell", "arguments": {"command": "ls"}, "id": "tc-1"}],
			reg, pm.plugins, ctx)
		assert not results[0].success
		assert "not allowed" in results[0].error

	@pytest.mark.asyncio
	async def test_tool_allowed_when_capability_in_set(self):
		"""Tool with capability in allowed_capabilities executes normally."""
		async def fake_read(args, ctx):
			return ToolOutput.text("file content")
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="file_read", execute=fake_read, capability=Capability.FILE_READ, is_read_only=True))
		ctx = ExecutionContext(
			config=ExecutionConfig(allowed_capabilities=frozenset({Capability.FILE_READ})),
			state=ExecutionState(),
		)
		pm = PluginManager([])
		results = await execute_tool_calls(
			[{"name": "file_read", "arguments": {"path": "test.py"}, "id": "tc-1"}],
			reg, pm.plugins, ctx)
		assert results[0].success

	@pytest.mark.asyncio
	async def test_tool_rejected_when_no_capabilities_configured(self):
		"""Tools with capabilities require explicit allowed_capabilities."""
		async def fake_shell(args, ctx):
			return ToolOutput.text("should not run")
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="shell", execute=fake_shell, capability=Capability.SHELL))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([])
		results = await execute_tool_calls(
			[{"name": "shell", "arguments": {"command": "ls"}, "id": "tc-1"}],
			reg, pm.plugins, ctx)
		assert not results[0].success
		assert "not allowed" in results[0].error

	@pytest.mark.asyncio
	async def test_tool_without_capability_always_allowed(self):
		"""Tools without capability field are always allowed regardless of config."""
		async def fake_tool(args, ctx):
			return ToolOutput.text("ok")
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="custom", execute=fake_tool))
		ctx = ExecutionContext(
			config=ExecutionConfig(allowed_capabilities=frozenset({Capability.FILE_READ})),
			state=ExecutionState(),
		)
		pm = PluginManager([])
		results = await execute_tool_calls(
			[{"name": "custom", "arguments": {}, "id": "tc-1"}],
			reg, pm.plugins, ctx)
		assert results[0].success
