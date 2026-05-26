"""Local command executor implementations.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import asyncio
import time

from axc_agent_engine.runtime.sandbox_models import CommandResult, CommandSpec
from axc_agent_engine.runtime.sandbox_utils import build_env, decode_limited, subprocess_preexec_fn


class LocalSubprocessExecutor:
	"""Local subprocess executor with cwd, env, timeout, and output limits.
中文：此文档说明相关引擎组件的行为。"""

	async def run(self, spec: CommandSpec) -> CommandResult:
		if spec.use_shell:
			if not spec.command:
				raise ValueError("command is required when use_shell=True")
		elif not spec.argv:
			raise ValueError("argv is required when use_shell=False")
		start = time.time()
		env = build_env(spec.env)
		preexec_fn = subprocess_preexec_fn()
		try:
			if spec.use_shell:
				proc = await asyncio.create_subprocess_shell(
					spec.command,
					stdout=asyncio.subprocess.PIPE,
					stderr=asyncio.subprocess.PIPE,
					cwd=spec.cwd or None,
					env=env,
					preexec_fn=preexec_fn,
				)
			else:
				proc = await asyncio.create_subprocess_exec(
					*spec.argv,
					stdout=asyncio.subprocess.PIPE,
					stderr=asyncio.subprocess.PIPE,
					cwd=spec.cwd or None,
					env=env,
					preexec_fn=preexec_fn,
				)
			stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=spec.timeout)
			duration_ms = int((time.time() - start) * 1000)
			stdout_str, stdout_truncated = decode_limited(stdout, spec.stdout_limit)
			stderr_str, stderr_truncated = decode_limited(stderr, spec.stderr_limit)
			return CommandResult(
				exit_code=proc.returncode or 0,
				stdout=stdout_str,
				stderr=stderr_str,
				duration_ms=duration_ms,
				stdout_truncated=stdout_truncated,
				stderr_truncated=stderr_truncated,
			)
		except asyncio.TimeoutError:
			proc.kill()
			try:
				await proc.wait()
			except Exception:
				pass
			duration_ms = int((time.time() - start) * 1000)
			return CommandResult(
				exit_code=-1,
				duration_ms=duration_ms,
				timed_out=True,
				stderr=f"Execution timeout ({spec.timeout}s)",
			)
