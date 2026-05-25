"""ToolOutput — 结构化工具结果协议。

所有工具都必须返回 ToolOutput；非 ToolOutput 返回会被拒绝。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ArtifactRef:
	"""已存储 artifact 的引用，大内容存储在外部。"""
	id: str
	kind: str  # text | file | json | table | binary
	size: int
	metadata: dict[str, Any] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		return {"id": self.id, "kind": self.kind, "size": self.size, "metadata": self.metadata}

	@classmethod
	def from_dict(cls, d: dict[str, Any]) -> "ArtifactRef":
		return cls(id=d["id"], kind=d["kind"], size=d["size"], metadata=d.get("metadata", {}))


@dataclass
class ToolOutput:
	"""结构化工具执行结果。

	所有工具都必须返回该结构，engine 会拒绝其他返回类型。
	"""
	content: str | dict | list
	content_type: str = "text"  # text | json | table | file | error
	summary: str = ""
	artifacts: list[ArtifactRef] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)
	is_error: bool = False

	def compact_view(self, max_chars: int = 2000) -> str:
		"""生成写入 message_store 的紧凑视图，不写入大原文。"""
		if self.is_error:
			content_str = self._content_as_str()
			return f"[Error] {content_str[:max_chars]}"
		if self.summary:
			view = self.summary
		else:
			view = self._content_as_str()
		if len(view) > max_chars:
			head = view[:int(max_chars * 0.75)]
			tail = view[-(max_chars - len(head)):]
			omitted = len(view) - len(head) - len(tail)
			view = f"{head}\n...[omitted {omitted} chars]...\n{tail}"
		if self.artifacts:
			refs = ", ".join(f"{a.kind}:{a.id}({a.size}B)" for a in self.artifacts)
			view += f"\n[artifacts: {refs}]"
		return view

	def _content_as_str(self) -> str:
		if isinstance(self.content, str):
			return self.content
		return json.dumps(self.content, ensure_ascii=False, default=str)

	def to_dict(self) -> dict[str, Any]:
		return {
			"content": self.content,
			"content_type": self.content_type,
			"summary": self.summary,
			"artifacts": [a.to_dict() for a in self.artifacts],
			"metadata": self.metadata,
			"is_error": self.is_error,
		}

	@classmethod
	def from_dict(cls, d: dict[str, Any]) -> "ToolOutput":
		return cls(
			content=d["content"],
			content_type=d.get("content_type", "text"),
			summary=d.get("summary", ""),
			artifacts=[ArtifactRef.from_dict(a) for a in d.get("artifacts", [])],
			metadata=d.get("metadata", {}),
			is_error=d.get("is_error", False),
		)

	@classmethod
	def text(cls, content: str, summary: str = "") -> "ToolOutput":
		"""便捷创建 text ToolOutput。"""
		return cls(content=content, content_type="text", summary=summary)

	@classmethod
	def json_output(cls, content: dict | list, summary: str = "") -> "ToolOutput":
		"""便捷创建 JSON ToolOutput。"""
		return cls(content=content, content_type="json", summary=summary)

	@classmethod
	def error(cls, message: str) -> "ToolOutput":
		"""便捷创建 error ToolOutput。"""
		return cls(content=message, content_type="error", is_error=True)


@runtime_checkable
class ResultStore(Protocol):
	"""外部存储大工具结果的协议。"""
	async def put(self, content: str | bytes, metadata: dict[str, Any] | None = None) -> ArtifactRef: ...
	async def get(self, artifact_id: str, offset: int = 0, limit: int = 4000) -> str: ...
	async def search(self, artifact_id: str, query: str) -> list[dict[str, Any]]: ...


def generate_artifact_id() -> str:
	"""生成唯一 artifact ID。"""
	return uuid.uuid4().hex[:16]
