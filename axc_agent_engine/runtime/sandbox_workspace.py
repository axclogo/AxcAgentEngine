"""Workspace-bounded command execution.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

from axc_agent_engine.runtime.sandbox_local import LocalSubprocessExecutor
from axc_agent_engine.runtime.sandbox_models import CommandExecutor, CommandResult, CommandSpec
from axc_agent_engine.runtime.sandbox_utils import write_text


class WorkspaceCommandExecutor:
	"""Command executor that keeps execution and logs inside one workspace.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(
		self,
		workspace: str,
		*,
		inner: CommandExecutor | None = None,
		log_dir: str = ".axc/logs",
	) -> None:
		if not workspace:
			raise ValueError("workspace is required")
		self.workspace = os.path.realpath(workspace)
		self.inner = inner or LocalSubprocessExecutor()
		self.log_dir = log_dir

	async def run(self, spec: CommandSpec) -> CommandResult:
		cwd = os.path.realpath(spec.cwd or self.workspace)
		if not (cwd == self.workspace or cwd.startswith(self.workspace + os.sep)):
			raise ValueError("Command cwd outside workspace boundary")
		os.makedirs(cwd, exist_ok=True)
		result = await self.inner.run(CommandSpec(
			argv=list(spec.argv),
			command=spec.command,
			cwd=cwd,
			timeout=spec.timeout,
			env=dict(spec.env),
			use_shell=spec.use_shell,
			stdout_limit=spec.stdout_limit,
			stderr_limit=spec.stderr_limit,
		))
		await asyncio.to_thread(self._write_log, spec, result, cwd)
		return result

	def _write_log(self, spec: CommandSpec, result: CommandResult, cwd: str) -> None:
		log_root = os.path.join(self.workspace, self.log_dir)
		os.makedirs(log_root, exist_ok=True)
		log_path = os.path.join(log_root, f"exec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.log")
		command = spec.command if spec.use_shell else " ".join(spec.argv)
		content = (
			f"cwd: {cwd}\n"
			f"command: {command}\n"
			f"exit_code: {result.exit_code}\n"
			f"duration_ms: {result.duration_ms}\n"
			f"timed_out: {result.timed_out}\n"
			"\n[stdout]\n"
			f"{result.stdout}\n"
			"\n[stderr]\n"
			f"{result.stderr}\n"
		)
		write_text(log_path, content)
