"""Policy evaluation for security-sensitive actions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from axc_agent_engine.core.errors import ErrorCategory, ErrorEnvelope


@dataclass(frozen=True)
class PolicyRequest:
	"""Input to a policy evaluator."""
	agent_name: str = ""
	session_id: str = ""
	tool_name: str = ""
	capability: str = ""
	risk_level: str = ""
	workspace: str = ""
	arguments: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
	"""Policy decision returned by a PolicyEvaluator."""
	allowed: bool
	reason: str = ""
	code: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)

	def to_error(self) -> ErrorEnvelope:
		return ErrorEnvelope(
			code=self.code or "policy.denied",
			message=self.reason or "Operation denied by policy",
			category=ErrorCategory.POLICY,
			details=self.metadata,
		)


@runtime_checkable
class PolicyEvaluator(Protocol):
	"""Evaluates whether an operation is allowed."""
	def evaluate(self, request: PolicyRequest) -> PolicyDecision: ...


class CapabilityPolicyEvaluator:
	"""Default policy evaluator based on allowed capability names."""

	def __init__(self, allowed_capabilities: set[str] | frozenset[str] | None = None) -> None:
		self._allowed_capabilities = frozenset(allowed_capabilities or [])

	def evaluate(self, request: PolicyRequest) -> PolicyDecision:
		if not request.capability:
			return PolicyDecision(allowed=True)
		if request.capability in self._allowed_capabilities:
			return PolicyDecision(allowed=True)
		return PolicyDecision(
			allowed=False,
			reason=f"Capability '{request.capability}' not allowed",
			code="policy.capability_not_allowed",
			metadata={
				"tool_name": request.tool_name,
				"capability": request.capability,
				"risk_level": request.risk_level,
				"agent_name": request.agent_name,
				"session_id": request.session_id,
			},
		)
