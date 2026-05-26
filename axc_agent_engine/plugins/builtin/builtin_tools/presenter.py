"""Presentation helpers for builtin command results.
中文：此文档说明相关引擎组件的行为。"""
from typing import Any

from axc_agent_engine.runtime.sandbox_models import CommandResult

from .result_store import ResultStoreReader


class BuiltinCommandPresenter:
	def __init__(self, result_reader: ResultStoreReader | None = None) -> None:
		self._result_reader = result_reader or ResultStoreReader()

	async def store_artifacts(
		self,
		result: CommandResult,
		context: dict[str, Any],
		stdout_limit: int = 1500,
		stderr_limit: int = 500,
	):
		artifacts = []
		result_store = self._result_reader.store(context)
		stdout_preview = result.stdout[:stdout_limit] if len(result.stdout) > stdout_limit else result.stdout
		stderr_preview = result.stderr[:stderr_limit] if len(result.stderr) > stderr_limit else result.stderr
		stdout_artifact_id = ""
		stderr_artifact_id = ""
		if result_store and len(result.stdout) > stdout_limit:
			ref = await result_store.put(result.stdout, {"kind": "text"})
			artifacts.append(ref)
			stdout_artifact_id = ref.id
		if result_store and len(result.stderr) > stderr_limit:
			ref = await result_store.put(result.stderr, {"kind": "text"})
			artifacts.append(ref)
			stderr_artifact_id = ref.id
		content_data: dict[str, Any] = {
			"exit_code": result.exit_code,
			"stdout_preview": stdout_preview,
			"stderr_preview": stderr_preview,
			"duration_ms": result.duration_ms,
			"timed_out": result.timed_out,
			"stdout_truncated": result.stdout_truncated,
			"stderr_truncated": result.stderr_truncated,
		}
		if stdout_artifact_id:
			content_data["stdout_artifact_id"] = stdout_artifact_id
		if stderr_artifact_id:
			content_data["stderr_artifact_id"] = stderr_artifact_id
		return content_data, artifacts
