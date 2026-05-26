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
from axc_agent_engine.runtime.sandbox_utils import build_env, decode_limited, subprocess_preexec_fn, write_text
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


async def test_local_subprocess_output_limit(tmp_path):
	executor = LocalSubprocessExecutor()
	result = await executor.run(CommandSpec(
		argv=[sys.executable, "-c", "print('x' * 100)"],
		cwd=str(tmp_path),
		timeout=10,
		stdout_limit=10,
	))
	assert result.stdout_truncated is True
	assert len(result.stdout) == 10


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


async def test_python_sandbox_uses_configured_command_executor(tmp_path):
    inner = RecordingExecutor()
    executor = PythonSandboxExecutor(str(tmp_path), command_executor=inner, python=sys.executable)
    result = await executor.run_code("print('x')")

    assert result.stdout == "ok"
    assert len(inner.specs) == 1
    assert inner.specs[0].argv[0] == sys.executable


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
	text, truncated = decode_limited("abcdef".encode(), 3)
	assert text == "abc"
	assert truncated is True
	text, truncated = decode_limited("abc".encode(), 0)
	assert text == "abc"
	assert truncated is False
	path = tmp_path / "x.txt"
	write_text(str(path), "hello")
	assert path.read_text() == "hello"
	if subprocess_preexec_fn() is not None:
		assert callable(subprocess_preexec_fn())
