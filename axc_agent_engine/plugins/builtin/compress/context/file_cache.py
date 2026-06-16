"""Recently read file cache used after context compression.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from axc_agent_engine.tools.tool_output import ToolOutput


@dataclass
class FileCacheEntry:
	"""One bounded file-read snapshot preserved across compression.
中文：此文档说明相关引擎组件的行为。"""

	path: str
	text: str
	start_line: int = 1
	end_line: int = 0
	total_lines: int = 0
	artifact_id: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)
	updated_at: float = field(default_factory=time.time)

	def to_dict(self) -> dict[str, Any]:
		return {
			"path": self.path,
			"text": self.text,
			"start_line": self.start_line,
			"end_line": self.end_line,
			"total_lines": self.total_lines,
			"artifact_id": self.artifact_id,
			"metadata": dict(self.metadata),
			"updated_at": self.updated_at,
		}

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> "FileCacheEntry":
		return cls(
			path=str(data.get("path") or ""),
			text=str(data.get("text") or ""),
			start_line=int(data.get("start_line", 1) or 1),
			end_line=int(data.get("end_line", 0) or 0),
			total_lines=int(data.get("total_lines", 0) or 0),
			artifact_id=str(data.get("artifact_id") or ""),
			metadata=dict(data.get("metadata") or {}),
			updated_at=float(data.get("updated_at", time.time())),
		)


class FileReadCache:
	"""Bounded in-memory file snapshots persisted by CompressionBoundary.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, max_files: int = 5) -> None:
		self.max_files = max(0, int(max_files))
		self._entries: list[FileCacheEntry] = []

	def update_from_tool(self, tool_name: str, arguments: dict[str, Any], output: ToolOutput) -> None:
		if self.max_files <= 0 or output.is_error or tool_name not in {"file_read", "read_file"}:
			return
		entry = _entry_from_output(arguments, output)
		if not entry or not entry.path or not entry.text:
			return
		self._entries = [item for item in self._entries if item.path != entry.path]
		self._entries.append(entry)
		self._trim()

	def load(self, entries: list[dict[str, Any]]) -> None:
		self._entries = [FileCacheEntry.from_dict(item) for item in entries if isinstance(item, dict)]
		self._entries = [item for item in self._entries if item.path and item.text]
		self._trim()

	def dump(self) -> list[dict[str, Any]]:
		return [entry.to_dict() for entry in self._entries]

	def message(self) -> dict[str, str] | None:
		if not self._entries:
			return None
		lines = [
			"[恢复的文件缓存]",
			"以下是压缩前保留的已读文件快照。请把它们当作缓存上下文；只有需要确认新鲜度时才重新读取。",
		]
		used = 0
		for entry in sorted(self._entries, key=lambda item: item.updated_at, reverse=True):
			header = _format_header(entry)
			text = entry.text
			used += len(header) + len(text)
			lines.extend([header, text])
		return {"role": "system", "content": "\n".join(lines)} if len(lines) > 2 else None

	def _trim(self) -> None:
		if self.max_files > 0 and len(self._entries) > self.max_files:
			self._entries = self._entries[-self.max_files:]


def _entry_from_output(arguments: dict[str, Any], output: ToolOutput) -> FileCacheEntry | None:
	path = str(arguments.get("path") or arguments.get("file_path") or "")
	content = output.content
	if isinstance(content, dict):
		path = str(content.get("path") or path)
		text = str(content.get("text") or "")
		artifact_id = str(content.get("artifact_id") or "")
		if not artifact_id and output.artifacts:
			artifact_id = output.artifacts[0].id
		return FileCacheEntry(
			path=path,
			text=text,
			start_line=int(content.get("start_line", 1) or 1),
			end_line=int(content.get("end_line", 0) or 0),
			total_lines=int(content.get("total_lines", 0) or 0),
			artifact_id=artifact_id,
			metadata=dict(output.metadata),
		)
	if isinstance(content, str):
		return FileCacheEntry(path=path, text=content, metadata=dict(output.metadata))
	return None


def _format_header(entry: FileCacheEntry) -> str:
	line_range = ""
	if entry.end_line:
		line_range = f":{entry.start_line}-{entry.end_line}"
	total = f", total_lines={entry.total_lines}" if entry.total_lines else ""
	artifact = f", artifact_id={entry.artifact_id}" if entry.artifact_id else ""
	return f"--- {entry.path}{line_range}{total}{artifact} ---"
