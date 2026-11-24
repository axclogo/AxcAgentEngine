"""Local foreground command execution. 本地前台命令执行器。"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
import time
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import BinaryIO

from axc_agent_engine.runtime.sandbox_models import CommandResult, CommandSpec
from axc_agent_engine.runtime.sandbox_utils import build_env, decode_output, subprocess_preexec_fn

INLINE_OUTPUT_BYTES = 1024 * 1024


def _collect(stream: BinaryIO, cwd: str, name: str) -> tuple[str, str]:
	size = os.fstat(stream.fileno()).st_size
	stream.seek(0)
	if size <= INLINE_OUTPUT_BYTES:
		return decode_output(stream.read(INLINE_OUTPUT_BYTES)), ""
	root = Path(cwd or ".").resolve()
	logs = (root / ".axc" / "logs").resolve()
	if not logs.is_relative_to(root):
		raise ValueError("Command log directory outside workspace")
	logs.mkdir(parents=True, exist_ok=True)
	with tempfile.NamedTemporaryFile(prefix=f"{name}-", suffix=".log", dir=logs, delete=False) as log:
		try:
			shutil.copyfileobj(stream, log)
		except OSError:
			log.close()
			os.unlink(log.name)
			raise
	return "", log.name


async def _terminate(proc: asyncio.subprocess.Process) -> None:
	if os.name == "posix":
		with suppress(ProcessLookupError):
			os.killpg(proc.pid, signal.SIGKILL)
	elif proc.returncode is None:
		killer = await asyncio.create_subprocess_exec(
			str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
			"/F",
			"/T",
			"/PID",
			str(proc.pid),
			stdout=asyncio.subprocess.DEVNULL,
			stderr=asyncio.subprocess.DEVNULL,
		)
		await killer.wait()
		if proc.returncode is None:
			with suppress(ProcessLookupError):
				proc.kill()
	await proc.wait()


class LocalSubprocessExecutor:
	"""Run foreground commands with bounded memory. 在内存有界的条件下运行前台命令。"""

	async def run(self, spec: CommandSpec) -> CommandResult:
		args = [spec.command] if spec.use_shell else spec.argv
		if not args or not args[0]:
			raise ValueError("command is required" if spec.use_shell else "argv is required")
		start = time.monotonic()
		spawn = asyncio.create_subprocess_shell if spec.use_shell else asyncio.create_subprocess_exec
		with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
			launch = asyncio.create_task(
				spawn(
					*args,
					stdout=out,
					stderr=err,
					cwd=spec.cwd or None,
					env=build_env(spec.env),
					preexec_fn=subprocess_preexec_fn(),
					start_new_session=os.name == "posix",
				)
			)
			try:
				proc = await asyncio.shield(launch)
				code = await asyncio.wait_for(proc.wait(), spec.timeout)
			except asyncio.CancelledError:
				await _terminate(await launch)
				raise
			except TimeoutError:
				await _terminate(await launch)
				return CommandResult(
					-1,
					stderr=f"Execution timeout ({spec.timeout}s)",
					duration_ms=int((time.monotonic() - start) * 1000),
					timed_out=True,
				)
			# Reclaim the foreground group on exit. 前台调用结束时回收同组后台进程。
			if os.name == "posix":
				await _terminate(proc)
			collect = asyncio.create_task(asyncio.to_thread(self._result, code, spec.cwd, out, err, start))
			try:
				return await asyncio.shield(collect)
			except asyncio.CancelledError:
				result = await collect
				for path in (result.stdout_path, result.stderr_path):
					if path:
						Path(path).unlink(missing_ok=True)
				raise

	@staticmethod
	def _result(code: int, cwd: str, out: BinaryIO, err: BinaryIO, start: float) -> CommandResult:
		with ExitStack() as pending:
			stdout, stdout_path = _collect(out, cwd, "stdout")
			if stdout_path:
				pending.callback(Path(stdout_path).unlink, missing_ok=True)
			stderr, stderr_path = _collect(err, cwd, "stderr")
			pending.pop_all()
		return CommandResult(
			code,
			stdout,
			stderr,
			int((time.monotonic() - start) * 1000),
			stdout_path=stdout_path,
			stderr_path=stderr_path,
		)
