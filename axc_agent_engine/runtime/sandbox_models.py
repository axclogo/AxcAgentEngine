"""Sandbox command data contracts and protocols.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from axc_agent_engine.runtime.risk import RiskAssessment


@dataclass(frozen=True)
class CommandSpec:
	"""Description of a command execution request.
中文：此文档说明相关引擎组件的行为。"""
	argv: list[str] = field(default_factory=list)
	command: str = ""
	cwd: str = ""
	timeout: int = 60
	env: dict[str, str] = field(default_factory=dict)
	use_shell: bool = False


@dataclass(frozen=True)
class CommandResult:
	"""Result returned by a CommandExecutor.
中文：此文档说明相关引擎组件的行为。"""
	exit_code: int
	stdout: str = ""
	stderr: str = ""
	duration_ms: int = 0
	timed_out: bool = False


@runtime_checkable
class CommandExecutor(Protocol):
	"""Executes commands behind a sandbox boundary.
中文：此文档说明相关引擎组件的行为。"""
	async def run(self, spec: CommandSpec) -> CommandResult: ...


@runtime_checkable
class CommandPolicy(Protocol):
	"""Assesses whether a command may execute.
中文：此文档说明相关引擎组件的行为。"""
	def assess(self, spec: CommandSpec) -> RiskAssessment: ...


@runtime_checkable
class SandboxProvider(Protocol):
	"""Factory for workspace-scoped sandbox executors.
中文：此文档说明相关引擎组件的行为。"""
	def command_executor(self) -> CommandExecutor: ...
	def python(self) -> object: ...
	def powershell(self) -> object: ...
