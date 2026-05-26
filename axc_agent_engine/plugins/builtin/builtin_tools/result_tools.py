"""Artifact result builtin tools.
中文：此文档说明相关引擎组件的行为。"""
from typing import Any

from axc_agent_engine.tools.tool_output import ToolOutput

from .result_store import ResultStoreReader


class BuiltinResultTools:
	def __init__(self, result_reader: ResultStoreReader | None = None) -> None:
		self._result_reader = result_reader or ResultStoreReader()

	async def read(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		artifact_id = args.get("artifact_id", "")
		offset = args.get("offset", 0)
		limit = args.get("limit", 4000)
		if not artifact_id:
			return ToolOutput.error("artifact_id cannot be empty")
		result_store = self._result_reader.store(context)
		if not result_store:
			return ToolOutput.error("ResultStore not available")
		content = await result_store.get(artifact_id, offset=offset, limit=limit)
		if not content:
			return ToolOutput.error(f"Artifact not found: {artifact_id}")
		return ToolOutput.text(content, summary=f"result_read：从偏移 {offset} 读取 {len(content)} 个字符")

	async def search(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		artifact_id = args.get("artifact_id", "")
		query = args.get("query", "")
		if not artifact_id:
			return ToolOutput.error("artifact_id cannot be empty")
		if not query:
			return ToolOutput.error("query cannot be empty")
		result_store = self._result_reader.store(context)
		if not result_store:
			return ToolOutput.error("ResultStore not available")
		results = await result_store.search(artifact_id, query)
		if not results:
			return ToolOutput.json_output({"matches": [], "query": query}, summary=f"未找到 '{query}' 的匹配项")
		return ToolOutput.json_output({"matches": results, "query": query}, summary=f"找到 {len(results)} 个 '{query}' 的匹配项")

	async def page(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		artifact_id = args.get("artifact_id", "")
		page = max(1, args.get("page", 1))
		page_size = args.get("page_size", 4000)
		if not artifact_id:
			return ToolOutput.error("artifact_id cannot be empty")
		result_store = self._result_reader.store(context)
		if not result_store:
			return ToolOutput.error("ResultStore not available")
		offset = (page - 1) * page_size
		content = await result_store.get(artifact_id, offset=offset, limit=page_size)
		if not content:
			return ToolOutput.error(f"No content at page {page}")
		return ToolOutput.text(content, summary=f"result_page：第 {page} 页，{len(content)} 个字符")
