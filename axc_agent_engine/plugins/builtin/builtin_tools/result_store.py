"""ResultStore adapter used by builtin tools."""
from typing import Any


class ResultStoreReader:
	def store(self, context: dict[str, Any]):
		return context.get("result_store")
