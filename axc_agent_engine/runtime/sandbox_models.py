"""Sandbox command data contracts and protocols. 沙箱命令的数据结构与接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from axc_agent_engine.runtime.risk import RiskAssessment


@dataclass(frozen=True)
class CommandSpec:
	"""Description of a command execution request. 命令执行请求的数据结构。"""

	argv: list[str] = field(default_factory=list)
	command: str = ""
	cwd: str = ""
	timeout: int = 60
	env: dict[str, str] = field(default_factory=dict)
	use_shell: bool = False


@dataclass(frozen=True)
class CommandResult:
	"""Result returned by a CommandExecutor. 命令执行器返回的结果。"""

	exit_code: int
	stdout: str = ""
	stderr: str = ""
	duration_ms: int = 0
	timed_out: bool = False
	stdout_path: str = ""
	stderr_path: str = ""


@runtime_checkable
class CommandExecutor(Protocol):
	"""Executes commands behind a sandbox boundary. 在沙箱边界内执行命令。"""

	async def run(self, spec: CommandSpec) -> CommandResult: ...


@runtime_checkable
class CommandPolicy(Protocol):
	"""Assesses whether a command may execute. 判断命令是否获准执行。"""

	def assess(self, spec: CommandSpec) -> RiskAssessment: ...


@runtime_checkable
class SandboxProvider(Protocol):
	"""Factory for workspace-scoped sandbox executors. 创建限定于工作区的沙箱执行器。"""

	def command_executor(self) -> CommandExecutor: ...
	def python(self) -> object: ...
	def powershell(self) -> object: ...
