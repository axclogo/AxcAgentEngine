"""Production artifact storage protocol and in-memory backend.
中文：生产级 artifact 存储协议和内存后端。"""
from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from axc_agent_engine.tools.tool_output import ArtifactRef, generate_artifact_id


@dataclass(frozen=True)
class ArtifactRead:
	content: str
	artifact_id: str
	offset: int = 0
	limit: int = 0
	next_offset: int | None = None
	size: int = 0
	eof: bool = True


@dataclass(frozen=True)
class ArtifactSearchMatch:
	line: int
	text: str
	offset: int = 0

	def to_dict(self) -> dict[str, Any]:
		return {"line": self.line, "text": self.text, "offset": self.offset}


@dataclass
class _ArtifactEntry:
	ref: ArtifactRef
	source_type: str
	content: str | bytes | None = None
	file_path: str = ""
	created_at: float = field(default_factory=time.time)
	accessed_at: float = field(default_factory=time.time)
	expires_at: float | None = None
	durable: bool = False
	run_id: str = ""


@runtime_checkable
class ArtifactStore(Protocol):
	"""Artifact backend owned by the host or by the default in-memory backend.
中文：由宿主或默认内存后端实现的 artifact 存储边界。"""

	async def put_text(
		self,
		content: str,
		metadata: dict[str, Any] | None = None,
		*,
		kind: str = "text",
		run_id: str = "",
		durable: bool = False,
		expires_at: float | None = None,
	) -> ArtifactRef: ...

	async def put_bytes(
		self,
		content: bytes,
		metadata: dict[str, Any] | None = None,
		*,
		kind: str = "binary",
		run_id: str = "",
		durable: bool = False,
		expires_at: float | None = None,
	) -> ArtifactRef: ...

	async def put_file_ref(
		self,
		path: str,
		metadata: dict[str, Any] | None = None,
		*,
		kind: str = "file",
		run_id: str = "",
		durable: bool = False,
		expires_at: float | None = None,
	) -> ArtifactRef: ...

	async def read(self, artifact_id: str, offset: int = 0, limit: int = 4000) -> ArtifactRead: ...
	async def read_page(self, artifact_id: str, page: int = 1, page_size: int = 4000) -> ArtifactRead: ...
	async def search(self, artifact_id: str, query: str, max_results: int = 20) -> list[ArtifactSearchMatch]: ...
	async def stat(self, artifact_id: str) -> ArtifactRef | None: ...
	async def delete(self, artifact_id: str) -> None: ...
	async def delete_run(self, run_id: str) -> None: ...
	async def gc(self, now: float | None = None) -> dict[str, int]: ...


class InMemoryArtifactStore:
	"""Bounded artifact backend for local development and tests.
中文：用于本地开发和测试的有界 artifact 后端。

	Text and bytes are stored in memory. File artifacts store only a file path
	reference and read/search the file lazily, so registering a 10GB file does
	not copy it into memory.
中文：文本和字节存入内存；文件 artifact 只保存路径引用，并在读取或搜索时惰性访问文件，因此注册 10GB 文件不会复制到内存。
	"""

	def __init__(
		self,
		max_entries: int = 500,
		max_bytes: int = 50 * 1024 * 1024,
		default_ttl: int = 3600,
	) -> None:
		self._entries: OrderedDict[str, _ArtifactEntry] = OrderedDict()
		self._max_entries = max_entries
		self._max_bytes = max_bytes
		self._default_ttl = default_ttl
		self._total_inline_bytes = 0
		self._lock = asyncio.Lock()

	async def put_text(
		self,
		content: str,
		metadata: dict[str, Any] | None = None,
		*,
		kind: str = "text",
		run_id: str = "",
		durable: bool = False,
		expires_at: float | None = None,
	) -> ArtifactRef:
		text = str(content)
		size = len(text.encode("utf-8"))
		return await self._put_entry(
			_ArtifactEntry(
				ref=self._ref(kind, size, metadata, run_id, durable, expires_at),
				source_type="text",
				content=text,
				expires_at=self._expires_at(durable, expires_at),
				durable=durable,
				run_id=run_id,
			)
		)

	async def put_bytes(
		self,
		content: bytes,
		metadata: dict[str, Any] | None = None,
		*,
		kind: str = "binary",
		run_id: str = "",
		durable: bool = False,
		expires_at: float | None = None,
	) -> ArtifactRef:
		payload = bytes(content)
		return await self._put_entry(
			_ArtifactEntry(
				ref=self._ref(kind, len(payload), metadata, run_id, durable, expires_at),
				source_type="bytes",
				content=payload,
				expires_at=self._expires_at(durable, expires_at),
				durable=durable,
				run_id=run_id,
			)
		)

	async def put_file_ref(
		self,
		path: str,
		metadata: dict[str, Any] | None = None,
		*,
		kind: str = "file",
		run_id: str = "",
		durable: bool = False,
		expires_at: float | None = None,
	) -> ArtifactRef:
		file_path = str(Path(path).expanduser())
		size = os.path.getsize(file_path)
		meta = {"path": file_path, **dict(metadata or {})}
		return await self._put_entry(
			_ArtifactEntry(
				ref=self._ref(kind, size, meta, run_id, durable, expires_at),
				source_type="file",
				file_path=file_path,
				expires_at=self._expires_at(durable, expires_at),
				durable=durable,
				run_id=run_id,
			)
		)

	async def read(self, artifact_id: str, offset: int = 0, limit: int = 4000) -> ArtifactRead:
		entry = await self._entry(artifact_id)
		if entry is None:
			return ArtifactRead("", artifact_id=artifact_id, offset=max(0, offset), limit=max(0, limit), size=0)
		safe_offset = max(0, int(offset or 0))
		safe_limit = max(0, int(limit or 0))
		content = self._read_entry(entry, safe_offset, safe_limit)
		next_offset = safe_offset + len(content.encode("utf-8"))
		eof = next_offset >= entry.ref.size or safe_limit == 0
		return ArtifactRead(
			content=content,
			artifact_id=artifact_id,
			offset=safe_offset,
			limit=safe_limit,
			next_offset=None if eof else next_offset,
			size=entry.ref.size,
			eof=eof,
		)

	async def read_page(self, artifact_id: str, page: int = 1, page_size: int = 4000) -> ArtifactRead:
		safe_page = max(1, int(page or 1))
		safe_page_size = max(0, int(page_size or 0))
		return await self.read(artifact_id, offset=(safe_page - 1) * safe_page_size, limit=safe_page_size)

	async def search(self, artifact_id: str, query: str, max_results: int = 20) -> list[ArtifactSearchMatch]:
		needle = str(query)
		if not needle:
			return []
		entry = await self._entry(artifact_id)
		if entry is None:
			return []
		limit = max(1, int(max_results or 20))
		results: list[ArtifactSearchMatch] = []
		for line_no, line, offset in self._iter_lines(entry):
			if needle.lower() in line.lower():
				results.append(ArtifactSearchMatch(line=line_no, text=line, offset=offset))
				if len(results) >= limit:
					break
		return results

	async def stat(self, artifact_id: str) -> ArtifactRef | None:
		entry = await self._entry(artifact_id)
		if entry is None:
			return None
		return ArtifactRef.from_dict(entry.ref.to_dict())

	async def delete(self, artifact_id: str) -> None:
		async with self._lock:
			self._delete_unlocked(artifact_id)

	async def delete_run(self, run_id: str) -> None:
		async with self._lock:
			for artifact_id, entry in list(self._entries.items()):
				if entry.run_id == run_id and not entry.durable:
					self._delete_unlocked(artifact_id)

	async def gc(self, now: float | None = None) -> dict[str, int]:
		async with self._lock:
			before = len(self._entries)
			self._evict_expired(now or time.time())
			self._evict_to_limits()
			return {"deleted": before - len(self._entries), **self.stats()}

	def stats(self) -> dict[str, int]:
		return {
			"entries": len(self._entries),
			"max_entries": self._max_entries,
			"inline_bytes": self._total_inline_bytes,
			"max_bytes": self._max_bytes,
			"default_ttl": self._default_ttl,
		}

	def has(self, artifact_id: str) -> bool:
		return artifact_id in self._entries and not self._is_expired(self._entries[artifact_id], time.time())

	async def _put_entry(self, entry: _ArtifactEntry) -> ArtifactRef:
		async with self._lock:
			self._evict_expired(time.time())
			self._entries[entry.ref.id] = entry
			self._entries.move_to_end(entry.ref.id)
			if entry.source_type in {"text", "bytes"}:
				self._total_inline_bytes += entry.ref.size
			self._evict_to_limits()
		return ArtifactRef.from_dict(entry.ref.to_dict())

	def _ref(
		self,
		kind: str,
		size: int,
		metadata: dict[str, Any] | None,
		run_id: str,
		durable: bool,
		expires_at: float | None,
	) -> ArtifactRef:
		meta = dict(metadata or {})
		if run_id:
			meta["run_id"] = run_id
		if durable:
			meta["durable"] = True
		actual_expires_at = self._expires_at(durable, expires_at)
		if actual_expires_at is not None:
			meta["expires_at"] = actual_expires_at
		return ArtifactRef(id=generate_artifact_id(), kind=kind, size=size, metadata=meta)

	def _expires_at(self, durable: bool, expires_at: float | None) -> float | None:
		if durable:
			return None
		if expires_at is not None:
			return float(expires_at)
		if self._default_ttl <= 0:
			return None
		return time.time() + self._default_ttl

	async def _entry(self, artifact_id: str) -> _ArtifactEntry | None:
		async with self._lock:
			entry = self._entries.get(artifact_id)
			if entry is None:
				return None
			if self._is_expired(entry, time.time()):
				self._delete_unlocked(artifact_id)
				return None
			entry.accessed_at = time.time()
			self._entries.move_to_end(artifact_id)
			return entry

	def _read_entry(self, entry: _ArtifactEntry, offset: int, limit: int) -> str:
		if limit == 0:
			return ""
		if entry.source_type == "file":
			with open(entry.file_path, "rb") as f:
				f.seek(offset)
				return f.read(limit).decode("utf-8", errors="replace")
		content = entry.content or ""
		if isinstance(content, bytes):
			return content[offset:offset + limit].decode("utf-8", errors="replace")
		return content[offset:offset + limit]

	def _iter_lines(self, entry: _ArtifactEntry):
		if entry.source_type == "file":
			offset = 0
			with open(entry.file_path, "rb") as f:
				for line_no, raw in enumerate(f, start=1):
					line = raw.decode("utf-8", errors="replace").rstrip("\n")
					yield line_no, line, offset
					offset += len(raw)
			return
		content = entry.content or ""
		if isinstance(content, bytes):
			content = content.decode("utf-8", errors="replace")
		offset = 0
		for line_no, line in enumerate(str(content).splitlines(), start=1):
			yield line_no, line, offset
			offset += len(line.encode("utf-8")) + 1

	def _is_expired(self, entry: _ArtifactEntry, now: float) -> bool:
		return bool(entry.expires_at is not None and now >= entry.expires_at)

	def _evict_expired(self, now: float) -> None:
		for artifact_id, entry in list(self._entries.items()):
			if self._is_expired(entry, now):
				self._delete_unlocked(artifact_id)

	def _evict_to_limits(self) -> None:
		while self._max_entries > 0 and len(self._entries) > self._max_entries:
			self._delete_oldest_non_durable()
		while self._max_bytes > 0 and self._total_inline_bytes > self._max_bytes and self._entries:
			self._delete_oldest_non_durable()

	def _delete_oldest_non_durable(self) -> None:
		for artifact_id, entry in self._entries.items():
			if not entry.durable:
				self._delete_unlocked(artifact_id)
				return
		return

	def _delete_unlocked(self, artifact_id: str) -> None:
		entry = self._entries.pop(artifact_id, None)
		if entry and entry.source_type in {"text", "bytes"}:
			self._total_inline_bytes = max(0, self._total_inline_bytes - entry.ref.size)
