"""Presentation helpers for builtin command results. 组织内置命令的展示结果。"""

from typing import Any

from axc_agent_engine.plugins.builtin.common import command_log_artifact
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
		content_data: dict[str, Any] = {
			"exit_code": result.exit_code,
			"duration_ms": result.duration_ms,
			"timed_out": result.timed_out,
		}
		for name, text, path in (
			("stdout", result.stdout, result.stdout_path),
			("stderr", result.stderr, result.stderr_path),
		):
			content_data[name] = text
			if path:
				content_data[name], ref = await command_log_artifact(path, artifact_store, {"stream": name})
			elif artifact_store and text:
				ref = await artifact_store.put_text(text, {"stream": name}, kind="text")
			else:
				continue
			artifacts.append(ref)
			content_data[f"{name}_artifact_id"] = ref.id
		return content_data, artifacts
