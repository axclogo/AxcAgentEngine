"""Language-specific sandbox executors.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import os
import sys
import tempfile

from axc_agent_engine.runtime.sandbox_local import LocalSubprocessExecutor
from axc_agent_engine.runtime.sandbox_models import CommandExecutor, CommandResult, CommandSpec
from axc_agent_engine.runtime.sandbox_workspace import WorkspaceCommandExecutor


class PythonSandboxExecutor:
	"""Run Python snippets as workspace-scoped temporary scripts.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(
		self,
		workspace: str,
		*,
		command_executor: CommandExecutor | None = None,
		python: str | None = None,
	) -> None:
		self.workspace = os.path.realpath(workspace)
		self.python = python or sys.executable
		self.command_executor = WorkspaceCommandExecutor(
			self.workspace,
			inner=command_executor or LocalSubprocessExecutor(),
		)

	async def run_code(self, code: str, *, timeout: int = 30) -> CommandResult:
		temp_dir = os.path.join(self.workspace, ".axc", "tmp")
		os.makedirs(temp_dir, exist_ok=True)
		fd, script_path = tempfile.mkstemp(prefix="python_", suffix=".py", dir=temp_dir, text=True)
		try:
			with os.fdopen(fd, "w", encoding="utf-8") as f:
				f.write(code)
			return await self.command_executor.run(CommandSpec(
				argv=[self.python, script_path],
				cwd=self.workspace,
				timeout=timeout,
			))
		finally:
			try:
				os.remove(script_path)
			except FileNotFoundError:
				pass


class PowerShellSandboxExecutor:
	"""Run PowerShell snippets as workspace-scoped temporary scripts.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(
		self,
		workspace: str,
		*,
		command_executor: CommandExecutor | None = None,
		executable: str = "pwsh",
	) -> None:
		self.workspace = os.path.realpath(workspace)
		self.executable = executable
		self.command_executor = WorkspaceCommandExecutor(
			self.workspace,
			inner=command_executor or LocalSubprocessExecutor(),
		)

	async def run_code(self, code: str, *, timeout: int = 60) -> CommandResult:
		temp_dir = os.path.join(self.workspace, ".axc", "tmp")
		os.makedirs(temp_dir, exist_ok=True)
		fd, script_path = tempfile.mkstemp(prefix="powershell_", suffix=".ps1", dir=temp_dir)
		try:
			with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
				f.write(code)
			return await self.command_executor.run(CommandSpec(
				argv=[self.executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path],
				cwd=self.workspace,
				timeout=timeout,
			))
		finally:
			try:
				os.remove(script_path)
			except FileNotFoundError:
				pass
