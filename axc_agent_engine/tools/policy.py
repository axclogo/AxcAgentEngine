"""Tool policy evaluation."""
from __future__ import annotations

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.runtime.policy import CapabilityPolicyEvaluator, PolicyDecision, PolicyRequest
from axc_agent_engine.tools.runtime import ToolCallRuntime


def evaluate_tool_policy(ctx: ExecutionContext, runtime: ToolCallRuntime) -> PolicyDecision:
	evaluator = ctx.services.policy_evaluator or CapabilityPolicyEvaluator(ctx.config.allowed_capabilities)
	return evaluator.evaluate(PolicyRequest(
		agent_name=runtime.actor,
		session_id=runtime.session_id,
		tool_name=runtime.name,
		capability=runtime.tool_def.capability,
		risk_level=runtime.tool_def.risk_level,
		workspace=ctx.config.workspace,
		arguments=runtime.arguments,
	))
