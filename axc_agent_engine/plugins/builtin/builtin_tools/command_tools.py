"""Command execution builtin tools. 内置命令执行工具。"""

import os
import re
import sys
from typing import Any

from axc_agent_engine.plugins.builtin.common import bounded_int
from axc_agent_engine.runtime.risk import check_shell_command
from axc_agent_engine.runtime.sandbox_code import PythonSandboxExecutor
from axc_agent_engine.runtime.sandbox_local import LocalSubprocessExecutor
from axc_agent_engine.runtime.sandbox_models import CommandResult, CommandSpec
from axc_agent_engine.tools.tool_output import ToolOutput

from .http_tools import MAX_TOOL_TIMEOUT
from .path_policy import BuiltinPathPolicy
from .presenter import BuiltinCommandPresenter


class BuiltinCommandTools:
	def __init__(
		self,
		path_policy: BuiltinPathPolicy | None = None,
		presenter: BuiltinCommandPresenter | None = None,
	) -> None:
		self._path_policy = path_policy or BuiltinPathPolicy()
		self._presenter = presenter or BuiltinCommandPresenter()

	async def python_exec(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		code = args.get("code", "")
		if not code.strip():
			return ToolOutput.error("code cannot be empty")
		workspace_or_error = self._path_policy.get_workspace(context, "python_exec")
		if isinstance(workspace_or_error, ToolOutput):
			return workspace_or_error
		workspace = workspace_or_error
		python_path = "python3"
		timeout = bounded_int(args.get("timeout", 30), 1, MAX_TOOL_TIMEOUT)
		try:
			if workspace:
				venv_dir = os.path.join(workspace, "venv")
				python_path = await ensure_venv(venv_dir, context)
			result = await PythonSandboxExecutor(
				workspace or os.getcwd(),
				command_executor=get_command_executor(context),
				python=python_path,
			).run_code(code, timeout=timeout)
			content_data, artifacts = await self._presenter.store_artifacts(result, context)
			summary = f"python_exec：exit_code={result.exit_code}"
			return ToolOutput(content=content_data, content_type="json", summary=summary, artifacts=artifacts)
		except (AttributeError, OSError, RuntimeError) as e:
			return ToolOutput.error(str(e))

	async def shell(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		command = args.get("command", "")
		if not command.strip():
			return ToolOutput.error("command cannot be empty")
		workspace_or_error = self._path_policy.get_workspace(context, "shell")
		if isinstance(workspace_or_error, ToolOutput):
			return workspace_or_error
		assessment = check_shell_command(command)
		if not assessment.allowed:
			return ToolOutput.error(f"Blocked dangerous command: {assessment.reason}")
		shell_type = args.get("shell_type", "auto")
		workspace = workspace_or_error
		if shell_type == "auto":
			shell_type = "powershell" if sys.platform == "win32" else "bash"
		timeout = bounded_int(args.get("timeout", 60), 1, MAX_TOOL_TIMEOUT)
		try:
			executor = get_command_executor(context)
			if shell_type == "powershell":
				spec = CommandSpec(
					argv=["powershell", "-NoProfile", "-Command", command], cwd=workspace, timeout=timeout
				)
			else:
				spec = CommandSpec(argv=["bash", "-lc", command], cwd=workspace, timeout=timeout)
			result = await executor.run(spec)
			content_data, artifacts = await self._presenter.store_artifacts(result, context)
			summary = f"shell：exit_code={result.exit_code}"
			return ToolOutput(content=content_data, content_type="json", summary=summary, artifacts=artifacts)
		except (AttributeError, OSError, RuntimeError) as e:
			return ToolOutput.error(str(e))

	async def grep(self, args: dict, context: dict) -> ToolOutput:
		workspace = self._path_policy.get_workspace(context, "file_grep")
		if isinstance(workspace, ToolOutput):
			return workspace
		argv = [
			"rg",
			"--no-config",
			"--threads",
			"1",
			"--no-heading",
			"--color",
			"never",
			"--line-number",
			"--with-filename",
			"-e",
			args["pattern"],
			"--",
			".",
		]
		try:
			result = await get_command_executor(context).run(CommandSpec(argv=argv, cwd=workspace))
		except FileNotFoundError as error:
			return ToolOutput.error(f"file_grep requires ripgrep (rg) in the configured command environment: {error}")
		if result.timed_out or result.exit_code not in (0, 1):
			return ToolOutput.error(
				result.stderr or result.stderr_path or f"rg failed with exit code {result.exit_code}"
			)
		if result.exit_code == 1:
			return ToolOutput.text("无匹配行")
		if result.stdout_path:
			try:
				payload, refs = await self._presenter.store_artifacts(result, context)
			except RuntimeError as error:
				return ToolOutput.error(str(error))
			return ToolOutput(content=payload, content_type="json", artifacts=refs)
		return ToolOutput.text(result.stdout)

	async def pip_install(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		package = args.get("package", "")
		if not package:
			return ToolOutput.error("package cannot be empty")
		if not re.match(r"^[a-zA-Z0-9_\-\[\]>=<.,]+$", package):
			return ToolOutput.error("Invalid package name format")
		workspace_or_error = self._path_policy.get_workspace(context, "pip_install")
		if isinstance(workspace_or_error, ToolOutput):
			return workspace_or_error
		workspace = workspace_or_error
		pip_path = "pip"
		try:
			if workspace:
				venv_dir = os.path.join(workspace, "venv")
				python_path = await ensure_venv(venv_dir, context)
				pip_path = os.path.join(os.path.dirname(python_path), "pip")
			executor = get_command_executor(context)
			result = await executor.run(
				CommandSpec(
					argv=[pip_path, "install", package],
					cwd=workspace,
					timeout=120,
				)
			)
			data, artifacts = await self._presenter.store_artifacts(result, context)
			data["returncode"] = data.pop("exit_code")
			return ToolOutput(
				content=data,
				content_type="json",
				summary=f"pip install {package}: returncode={result.exit_code}",
				artifacts=artifacts,
			)
		except (AttributeError, OSError, RuntimeError) as e:
			return ToolOutput.error(str(e))


async def ensure_venv(venv_dir: str, context: dict[str, Any]) -> str:
	"""Ensure a venv exists and return its python executable path. 确保虚拟环境存在并返回其 Python 路径。"""
	if sys.platform == "win32":
		python_path = os.path.join(venv_dir, "Scripts", "python.exe")
	else:
		python_path = os.path.join(venv_dir, "bin", "python")
	if os.path.exists(python_path):
		return python_path
	os.makedirs(venv_dir, exist_ok=True)
	executor = get_command_executor(context)
	result = await executor.run(
		CommandSpec(
			argv=[sys.executable, "-m", "venv", venv_dir],
			cwd=context.get("workspace") or None,
			timeout=120,
		)
	)
	if result.exit_code != 0:
		raise RuntimeError(result.stderr or result.stderr_path or f"venv creation failed: exit_code={result.exit_code}")
	return python_path


def get_command_executor(context: dict[str, Any]):
	"""Return configured command executor or the local restricted baseline. 选择配置的执行器或本地受限执行器。"""
	return context.get("command_executor") or LocalSubprocessExecutor()


async def store_command_artifacts(
	result: CommandResult,
	context: dict[str, Any],
):
	"""Build command output content and artifact refs. 构建命令结果内容和产物引用。"""
	return await BuiltinCommandPresenter().store_artifacts(result, context)
