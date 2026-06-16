"""Tests for command sandbox abstractions."""
from __future__ import annotations

import sys

import pytest

from axc_agent_engine.runtime.sandbox_code import PowerShellSandboxExecutor, PythonSandboxExecutor
from axc_agent_engine.runtime.sandbox_local import LocalSubprocessExecutor
from axc_agent_engine.runtime.sandbox_models import CommandExecutor, CommandResult, CommandSpec
from axc_agent_engine.runtime.sandbox_policy import DefaultCommandPolicy, PolicyCommandExecutor
from axc_agent_engine.runtime.sandbox_provider import LocalSandboxProvider
from axc_agent_engine.runtime.sandbox_workspace import WorkspaceCommandExecutor
from axc_agent_engine.runtime.sandbox_utils import build_env, decode_output, subprocess_preexec_fn, write_text
from axc_agent_engine.core.schema import RiskLevel


async def test_local_subprocess_exec_runs_argv(tmp_path):
	executor = LocalSubprocessExecutor()
	result = await executor.run(CommandSpec(
		argv=[sys.executable, "-c", "print('hello')"],
		cwd=str(tmp_path),
		timeout=10,
	))
	assert result.exit_code == 0
	assert result.stdout.strip() == "hello"
	assert result.stderr == ""
	assert result.duration_ms >= 0


async def test_local_subprocess_shell_runs_command(tmp_path):
	executor = LocalSubprocessExecutor()
	result = await executor.run(CommandSpec(command="echo shell-ok", use_shell=True, cwd=str(tmp_path), timeout=10))
	assert result.exit_code == 0
	assert "shell-ok" in result.stdout


async def test_local_subprocess_timeout(tmp_path):
	executor = LocalSubprocessExecutor()
	result = await executor.run(CommandSpec(
		argv=[sys.executable, "-c", "import time; time.sleep(2)"],
		cwd=str(tmp_path),
		timeout=1,
	))
	assert result.timed_out is True
	assert result.exit_code == -1
	assert "timeout" in result.stderr.lower()


async def test_local_subprocess_returns_full_output(tmp_path):
	executor = LocalSubprocessExecutor()
	result = await executor.run(CommandSpec(
		argv=[sys.executable, "-c", "print('x' * 100)"],
		cwd=str(tmp_path),
		timeout=10,
	))
	assert result.stdout == "x" * 100 + "\n"


def test_command_executor_protocol():
    assert isinstance(LocalSubprocessExecutor(), CommandExecutor)


async def test_workspace_executor_rejects_cwd_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    executor = WorkspaceCommandExecutor(str(workspace))

    with pytest.raises(ValueError, match="outside workspace"):
        await executor.run(CommandSpec(argv=[sys.executable, "-c", "print('x')"], cwd=str(outside)))


async def test_workspace_executor_writes_execution_log(tmp_path):
    executor = WorkspaceCommandExecutor(str(tmp_path))
    result = await executor.run(CommandSpec(argv=[sys.executable, "-c", "print('logged')"], timeout=10))

    assert result.exit_code == 0
    logs = list((tmp_path / ".axc" / "logs").glob("exec_*.log"))
    assert len(logs) == 1
    assert "logged" in logs[0].read_text(encoding="utf-8")


async def test_python_sandbox_runs_script_and_removes_temp_file(tmp_path):
    executor = PythonSandboxExecutor(str(tmp_path), python=sys.executable)
    result = await executor.run_code("print('sandbox-ok')", timeout=10)

    assert result.exit_code == 0
    assert result.stdout.strip() == "sandbox-ok"
    temp_dir = tmp_path / ".axc" / "tmp"
    assert list(temp_dir.glob("python_*.py")) == []


class RecordingExecutor:
    def __init__(self) -> None:
        self.specs: list[CommandSpec] = []

    async def run(self, spec: CommandSpec) -> CommandResult:
        self.specs.append(spec)
        return CommandResult(exit_code=0, stdout="ok")


class RemovingExecutor(RecordingExecutor):
	async def run(self, spec: CommandSpec) -> CommandResult:
		self.specs.append(spec)
		if spec.argv:
			from pathlib import Path
			Path(spec.argv[-1]).unlink()
		return CommandResult(exit_code=0, stdout="removed")


async def test_python_sandbox_uses_configured_command_executor(tmp_path):
    inner = RecordingExecutor()
    executor = PythonSandboxExecutor(str(tmp_path), command_executor=inner, python=sys.executable)
    result = await executor.run_code("print('x')")

    assert result.stdout == "ok"
    assert len(inner.specs) == 1
    assert inner.specs[0].argv[0] == sys.executable


async def test_python_and_powershell_sandbox_ignore_missing_temp_on_cleanup(tmp_path):
	python_inner = RemovingExecutor()
	python_executor = PythonSandboxExecutor(str(tmp_path), command_executor=python_inner, python=sys.executable)
	assert (await python_executor.run_code("print('x')")).stdout == "removed"

	powershell_inner = RemovingExecutor()
	powershell_executor = PowerShellSandboxExecutor(str(tmp_path), command_executor=powershell_inner, executable="pwsh")
	assert (await powershell_executor.run_code("Write-Output x")).stdout == "removed"


async def test_policy_command_executor_blocks_disallowed_shell(tmp_path):
    executor = PolicyCommandExecutor(LocalSubprocessExecutor(), DefaultCommandPolicy(max_risk=RiskLevel.MODERATE))
    result = await executor.run(CommandSpec(command="sudo rm -rf /tmp/x", use_shell=True, cwd=str(tmp_path)))
    assert result.exit_code == 126
    assert "blocked" in result.stderr.lower()


async def test_local_sandbox_provider_runs_python(tmp_path):
    provider = LocalSandboxProvider(str(tmp_path))
    result = await provider.python().run_code("print('provider-ok')", timeout=10)
    assert result.exit_code == 0
    assert result.stdout.strip() == "provider-ok"


async def test_powershell_sandbox_uses_configured_executor_and_removes_temp(tmp_path):
	inner = RecordingExecutor()
	executor = PowerShellSandboxExecutor(str(tmp_path), command_executor=inner, executable="pwsh")
	result = await executor.run_code("Write-Output ok")
	assert result.stdout == "ok"
	assert inner.specs[0].argv[:4] == ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass"]
	assert list((tmp_path / ".axc" / "tmp").glob("powershell_*.ps1")) == []


def test_sandbox_utils_env_decode_write_and_preexec(tmp_path, monkeypatch):
	monkeypatch.setenv("PATH", "/bin")
	monkeypatch.setenv("SECRET", "no")
	env = build_env({"PATH": "/custom", "SECRET": "still-no", "HOME": "/home/test"})
	assert env["PATH"] == "/custom"
	assert env["HOME"] == "/home/test"
	assert "SECRET" not in env
	text = decode_output("abcdef".encode())
	assert text == "abcdef"
	text = decode_output("abc".encode())
	assert text == "abc"
	path = tmp_path / "x.txt"
	write_text(str(path), "hello")
	assert path.read_text() == "hello"
	if subprocess_preexec_fn() is not None:
		assert callable(subprocess_preexec_fn())


def test_sandbox_utils_decode_invalid_utf8_and_no_truncation():
	text = decode_output(b"\xffabc")
	assert text.startswith("\ufffd")
	text = decode_output(b"\xffabc")
	assert text.startswith("\ufffd")
	assert text.endswith("abc")


def test_sandbox_utils_build_env_ignores_empty_and_unknown(monkeypatch):
	for key in ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR"):
		monkeypatch.delenv(key, raising=False)
	env = build_env({"UNKNOWN": "x", "PATH": "/bin"})
	assert env == {"PATH": "/bin"}


def test_sandbox_preexec_returns_none_on_windows(monkeypatch):
	monkeypatch.setattr("axc_agent_engine.runtime.sandbox_utils.sys.platform", "win32")
	assert subprocess_preexec_fn() is None


def test_sandbox_set_limits_ignores_resource_errors(monkeypatch):
	import axc_agent_engine.runtime.sandbox_utils as sandbox_utils

	if sandbox_utils._set_limits is None:
		return
	calls = []

	def fail_setrlimit(limit, values):
		calls.append((limit, values))
		if len(calls) == 1:
			raise ValueError("bad limit")
		raise OSError("blocked")

	monkeypatch.setattr(sandbox_utils.resource, "setrlimit", fail_setrlimit)
	sandbox_utils._set_limits()
	assert len(calls) == 3
