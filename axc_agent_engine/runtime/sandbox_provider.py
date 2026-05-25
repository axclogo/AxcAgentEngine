"""Sandbox provider factories."""
from __future__ import annotations

import os

from axc_agent_engine.runtime.sandbox_code import PowerShellSandboxExecutor, PythonSandboxExecutor
from axc_agent_engine.runtime.sandbox_local import LocalSubprocessExecutor
from axc_agent_engine.runtime.sandbox_models import CommandExecutor, CommandPolicy
from axc_agent_engine.runtime.sandbox_policy import DefaultCommandPolicy, PolicyCommandExecutor
from axc_agent_engine.runtime.sandbox_workspace import WorkspaceCommandExecutor


class LocalSandboxProvider:
	"""Default workspace sandbox provider with command policy enforcement."""

	def __init__(self, workspace: str, *, policy: CommandPolicy | None = None) -> None:
		self.workspace = os.path.realpath(workspace)
		self.policy = policy or DefaultCommandPolicy()

	def command_executor(self) -> CommandExecutor:
		return WorkspaceCommandExecutor(
			self.workspace,
			inner=PolicyCommandExecutor(LocalSubprocessExecutor(), self.policy),
		)

	def python(self) -> PythonSandboxExecutor:
		return PythonSandboxExecutor(self.workspace, command_executor=self.command_executor())

	def powershell(self) -> PowerShellSandboxExecutor:
		return PowerShellSandboxExecutor(
			self.workspace,
			command_executor=self.command_executor(),
		)
