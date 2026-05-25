"""Tests for policy evaluation."""
from __future__ import annotations

from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionServices
from axc_agent_engine.runtime.policy import CapabilityPolicyEvaluator, PolicyDecision, PolicyEvaluator, PolicyRequest
from axc_agent_engine.core.schema import Capability, ToolDefinition
from axc_agent_engine.tools.orchestrator import execute_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


def test_capability_policy_allows_when_no_capability():
	decision = CapabilityPolicyEvaluator({Capability.SHELL}).evaluate(PolicyRequest(tool_name="x"))
	assert decision.allowed is True


def test_capability_policy_denies_missing_capability():
	decision = CapabilityPolicyEvaluator({Capability.FILE_READ}).evaluate(PolicyRequest(
		tool_name="shell",
		capability=Capability.SHELL,
		risk_level="dangerous",
		agent_name="agent",
		session_id="session",
	))
	assert decision.allowed is False
	assert decision.code == "policy.capability_not_allowed"
	error = decision.to_error()
	assert error.code == "policy.capability_not_allowed"
	assert error.details["capability"] == Capability.SHELL


def test_capability_policy_denies_when_allowed_set_empty():
	decision = CapabilityPolicyEvaluator().evaluate(PolicyRequest(
		tool_name="shell",
		capability=Capability.SHELL,
		risk_level="dangerous",
	))
	assert decision.allowed is False
	assert decision.code == "policy.capability_not_allowed"


async def test_custom_policy_evaluator_blocks_tool_and_is_audited():
	class DenyAllPolicy:
		def evaluate(self, request: PolicyRequest) -> PolicyDecision:
			return PolicyDecision(
				allowed=False,
				reason=f"blocked {request.tool_name}",
				code="policy.custom_block",
				metadata={"tool_name": request.tool_name},
			)

	async def echo(args, ctx):
		return ToolOutput.text("should not run")

	registry = ToolRegistry()
	registry.register(ToolDefinition(name="echo", execute=echo))
	audit = InMemoryAuditSink()
	ctx = ExecutionContext(
		services=ExecutionServices(policy_evaluator=DenyAllPolicy(), audit_sink=audit),
	)

	results = await execute_tool_calls(
		[{"name": "echo", "arguments": {}, "id": "policy-1"}],
		registry,
		[],
		ctx,
	)

	assert not results[0].success
	assert results[0].error == "blocked echo"
	events = await audit.list_events()
	assert events[0].error["code"] == "policy.custom_block"


async def test_default_policy_preserves_allowed_capabilities_behavior():
	async def shell(args, ctx):
		return ToolOutput.text("ok")

	registry = ToolRegistry()
	registry.register(ToolDefinition(name="shell", execute=shell, capability=Capability.SHELL))
	ctx = ExecutionContext(config=ExecutionConfig(allowed_capabilities=frozenset({Capability.SHELL})))

	results = await execute_tool_calls(
		[{"name": "shell", "arguments": {}, "id": "policy-2"}],
		registry,
		[],
		ctx,
	)

	assert results[0].success


def test_policy_evaluator_protocol():
	assert isinstance(CapabilityPolicyEvaluator(), PolicyEvaluator)
