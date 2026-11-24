"""Policy evaluation for security-sensitive actions. 敏感操作的权限策略评估。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from axc_agent_engine.core.errors import ErrorCategory, ErrorEnvelope


@dataclass(frozen=True)
class PolicyRequest:
	"""Input to a policy evaluator. 策略评估的输入。"""

	agent_name: str = ""
	session_id: str = ""
	tool_name: str = ""
	capability: str = ""
	risk_level: str = ""
	workspace: str = ""
	arguments: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)

	def __post_init__(self) -> None:
		object.__setattr__(self, "arguments", deepcopy(self.arguments))
		object.__setattr__(self, "metadata", deepcopy(self.metadata))


@dataclass(frozen=True)
class PolicyDecision:
	"""Policy decision returned by a PolicyEvaluator. 策略评估器返回的决定。"""

	allowed: bool
	reason: str = ""
	code: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)

	def __post_init__(self) -> None:
		object.__setattr__(self, "metadata", deepcopy(self.metadata))

	def to_error(self) -> ErrorEnvelope:
		return ErrorEnvelope(
			code=self.code or "policy.denied",
			message=self.reason or "Operation denied by policy",
			category=ErrorCategory.POLICY,
			details=self.metadata,
		)


@runtime_checkable
class PolicyEvaluator(Protocol):
	"""Evaluates whether an operation is allowed. 判断操作是否获准。"""

	def evaluate(self, request: PolicyRequest) -> PolicyDecision: ...


class CapabilityPolicyEvaluator:
	"""Default policy evaluator based on allowed capability names. 根据允许的能力名判断操作权限。"""

	def __init__(self, allowed_capabilities: set[str] | frozenset[str] | None = None) -> None:
		self._allowed_capabilities = frozenset(allowed_capabilities or [])

	def allows_capability(self, capability: str) -> bool:
		return not capability or capability in self._allowed_capabilities

	def evaluate(self, request: PolicyRequest) -> PolicyDecision:
		if self.allows_capability(request.capability):
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
