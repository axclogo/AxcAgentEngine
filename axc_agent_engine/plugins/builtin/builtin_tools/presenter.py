"""Presentation helpers for builtin command results.
中文：此文档说明相关引擎组件的行为。"""
from typing import Any

from axc_agent_engine.runtime.sandbox_models import CommandResult

from .artifact_store import ArtifactStoreReader


class BuiltinCommandPresenter:
	def __init__(self, artifact_reader: ArtifactStoreReader | None = None) -> None:
		self._artifact_reader = artifact_reader or ArtifactStoreReader()

	async def store_artifacts(
		self,
		result: CommandResult,
		context: dict[str, Any],
	):
		artifacts = []
		artifact_store = self._artifact_reader.store(context)
		stdout_artifact_id = ""
		stderr_artifact_id = ""
		if artifact_store and result.stdout:
			ref = await artifact_store.put_text(result.stdout, {"stream": "stdout"}, kind="text")
			artifacts.append(ref)
			stdout_artifact_id = ref.id
		if artifact_store and result.stderr:
			ref = await artifact_store.put_text(result.stderr, {"stream": "stderr"}, kind="text")
			artifacts.append(ref)
			stderr_artifact_id = ref.id
		content_data: dict[str, Any] = {
			"exit_code": result.exit_code,
			"stdout": result.stdout,
			"stderr": result.stderr,
			"duration_ms": result.duration_ms,
			"timed_out": result.timed_out,
		}
		if stdout_artifact_id:
			content_data["stdout_artifact_id"] = stdout_artifact_id
		if stderr_artifact_id:
			content_data["stderr_artifact_id"] = stderr_artifact_id
		return content_data, artifacts
