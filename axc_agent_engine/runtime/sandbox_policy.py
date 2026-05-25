"""Risk policy wrapper for sandbox command execution."""
from __future__ import annotations

from axc_agent_engine.core.schema import RiskLevel
from axc_agent_engine.runtime.risk import RISK_LEVELS, RiskAssessment, check_shell_command
from axc_agent_engine.runtime.sandbox_local import LocalSubprocessExecutor
from axc_agent_engine.runtime.sandbox_models import CommandExecutor, CommandPolicy, CommandResult, CommandSpec


class DefaultCommandPolicy:
	"""Dynamic shell risk policy for command executors."""

	def __init__(self, max_risk: RiskLevel = RiskLevel.MODERATE, allow_shell: bool = True) -> None:
		self.max_risk = max_risk
		self.allow_shell = allow_shell

	def assess(self, spec: CommandSpec) -> RiskAssessment:
		command = spec.command if spec.use_shell else " ".join(spec.argv)
		if spec.use_shell and not self.allow_shell:
			return RiskAssessment(RiskLevel.BLOCKED, "shell execution disabled", allowed=False)
		assessment = check_shell_command(command)
		if RISK_LEVELS[assessment.level] > RISK_LEVELS[self.max_risk]:
			return RiskAssessment(
				RiskLevel.BLOCKED,
				assessment.reason or f"risk {assessment.level} exceeds policy {self.max_risk}",
				assessment.matched_rule,
				allowed=False,
			)
		return assessment


class PolicyCommandExecutor:
	"""CommandExecutor wrapper that enforces a CommandPolicy before execution."""

	def __init__(self, inner: CommandExecutor | None = None, policy: CommandPolicy | None = None) -> None:
		self.inner = inner or LocalSubprocessExecutor()
		self.policy = policy or DefaultCommandPolicy()

	async def run(self, spec: CommandSpec) -> CommandResult:
		assessment = self.policy.assess(spec)
		if not assessment.allowed:
			return CommandResult(exit_code=126, stderr=f"Command blocked: {assessment.reason}")
		return await self.inner.run(spec)
