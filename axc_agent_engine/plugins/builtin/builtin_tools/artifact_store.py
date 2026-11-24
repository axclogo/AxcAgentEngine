"""ArtifactStore adapter used by builtin tools. 内置工具使用的产物存储适配器。"""

from typing import Any


class ArtifactStoreReader:
	def store(self, context: dict[str, Any]):
		return context.get("artifact_store")
