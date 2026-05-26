"""ResultStore adapter used by builtin tools.
中文：此文档说明相关引擎组件的行为。"""
from typing import Any


class ResultStoreReader:
	def store(self, context: dict[str, Any]):
		return context.get("result_store")
