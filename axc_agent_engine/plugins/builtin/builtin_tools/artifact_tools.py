"""Artifact builtin tools.
中文：此文档说明相关引擎组件的行为。"""
from typing import Any

from axc_agent_engine.tools.tool_output import ToolOutput

from .artifact_store import ArtifactStoreReader


class BuiltinArtifactTools:
	def __init__(self, artifact_reader: ArtifactStoreReader | None = None) -> None:
		self._artifact_reader = artifact_reader or ArtifactStoreReader()

	async def read(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		artifact_id = args.get("artifact_id", "")
		offset = args.get("offset", 0)
		limit = args.get("limit", 4000)
		if not artifact_id:
			return ToolOutput.error("artifact_id cannot be empty")
		artifact_store = self._artifact_reader.store(context)
		if not artifact_store:
			return ToolOutput.error("ArtifactStore not available")
		read = await artifact_store.read(artifact_id, offset=offset, limit=limit)
		if not read.content:
			return ToolOutput.error(f"Artifact not found: {artifact_id}")
		return ToolOutput.text(
			read.content,
			summary=f"artifact_read：从偏移 {offset} 读取 {len(read.content)} 个字符",
			llm_view=_artifact_read_llm_view(read),
		)

	async def search(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		artifact_id = args.get("artifact_id", "")
		query = args.get("query", "")
		if not artifact_id:
			return ToolOutput.error("artifact_id cannot be empty")
		if not query:
			return ToolOutput.error("query cannot be empty")
		artifact_store = self._artifact_reader.store(context)
		if not artifact_store:
			return ToolOutput.error("ArtifactStore not available")
		results = await artifact_store.search(artifact_id, query)
		rows = [_match_to_dict(item) for item in results]
		if not results:
			return ToolOutput(
				content={"matches": [], "query": query},
				content_type="json",
				summary=f"未找到 '{query}' 的匹配项",
				llm_view=f"No matches for query: {query}",
			)
		return ToolOutput(
			content={"matches": rows, "query": query},
			content_type="json",
			summary=f"找到 {len(results)} 个 '{query}' 的匹配项",
			llm_view=_artifact_search_llm_view(query, rows),
		)

	async def page(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		artifact_id = args.get("artifact_id", "")
		page = max(1, args.get("page", 1))
		page_size = args.get("page_size", 4000)
		if not artifact_id:
			return ToolOutput.error("artifact_id cannot be empty")
		artifact_store = self._artifact_reader.store(context)
		if not artifact_store:
			return ToolOutput.error("ArtifactStore not available")
		read = await artifact_store.read_page(artifact_id, page=page, page_size=page_size)
		if not read.content:
			return ToolOutput.error(f"No content at page {page}")
		return ToolOutput.text(
			read.content,
			summary=f"artifact_page：第 {page} 页，{len(read.content)} 个字符",
			llm_view=_artifact_read_llm_view(read),
		)


def _artifact_read_llm_view(read: Any) -> str:
	lines = [
		f"Artifact: {read.artifact_id}",
		f"Offset: {read.offset}",
		f"Size: {read.size}",
		f"Next offset: {read.next_offset if read.next_offset is not None else '<eof>'}",
		"内容:",
		read.content,
	]
	return "\n".join(lines)


def _artifact_search_llm_view(query: str, results: list[dict[str, Any]]) -> str:
	lines = [f"Search results for: {query}", f"Matches: {len(results)}"]
	for index, item in enumerate(results, start=1):
		offset = item.get("offset", item.get("position", ""))
		preview = str(item.get("preview", item.get("text", item.get("content", "")))).strip()
		location = f" at offset {offset}" if offset != "" else ""
		lines.append(f"{index}. Match{location}")
		if preview:
			lines.append(f"   {preview}")
	return "\n".join(lines)


def _match_to_dict(item: Any) -> dict[str, Any]:
	if hasattr(item, "to_dict"):
		return item.to_dict()
	return dict(item)
