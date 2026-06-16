"""ToolOutput — 结构化工具结果协议。

所有工具都必须返回 ToolOutput；非 ToolOutput 返回会被拒绝。
"""
from __future__ import annotations

import json
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArtifactRef:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
已存储 artifact 的引用，大内容存储在外部。"""
	id: str
	kind: str  # English: text | file | json | table | binary. 中文：artifact 类型枚举。
	size: int
	metadata: dict[str, Any] = field(default_factory=dict)

	def __post_init__(self) -> None:
		self.metadata = deepcopy(self.metadata)

	def to_dict(self) -> dict[str, Any]:
		return {"id": self.id, "kind": self.kind, "size": self.size, "metadata": deepcopy(self.metadata)}

	@classmethod
	def from_dict(cls, d: dict[str, Any]) -> "ArtifactRef":
		return cls(id=d["id"], kind=d["kind"], size=d["size"], metadata=deepcopy(d.get("metadata", {})))


@dataclass
class ToolOutput:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
结构化工具执行结果。

	所有工具都必须返回该结构，engine 会拒绝其他返回类型。
	"""
	content: str | dict | list
	content_type: str = "text"  # English: text | json | table | file | error. 中文：内容类型枚举。
	summary: str = ""
	llm_view: str = ""
	artifacts: list[ArtifactRef] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)
	is_error: bool = False

	def __post_init__(self) -> None:
		self.content = deepcopy(self.content)
		self.llm_view = str(self.llm_view) if self.llm_view else ""
		self.artifacts = [ArtifactRef.from_dict(artifact.to_dict()) for artifact in self.artifacts]
		self.metadata = deepcopy(self.metadata)

	def context_view(self, max_chars: int = 0) -> str:
		"""English: LLM context view. 中文：写入 LLM 上下文的完整工具结果视图。"""
		if self.is_error:
			content_str = self._content_as_str()
			return f"[错误] {content_str}"
		durable = self.durable_summary()
		if durable:
			view = durable
		elif self.llm_view:
			view = self.llm_view
		else:
			view = self._content_as_str()
		if self.artifacts:
			refs = ", ".join(f"{a.kind}:{a.id}({a.size}B)" for a in self.artifacts)
			view += f"\n[附件：{refs}]"
		return view

	def display_view(self, max_chars: int = 0) -> str:
		"""English: UI/event display view. 中文：用于事件和 UI 展示的结果视图。"""
		return self._content_as_str()

	def durable_summary(self, max_chars: int = 4000) -> str:
		"""English: Return long-lived tool facts. 中文：返回后续轮次必须保留的工具结果摘要。"""
		value = self.metadata.get("durable_summary", "")
		if not value:
			return ""
		return str(value)

	def is_durable(self) -> bool:
		"""English: Whether this result should survive compression. 中文：结果是否应跨压缩保留。"""
		return bool(self.metadata.get("durable") or self.metadata.get("durable_summary"))

	def with_metadata(self, metadata: dict[str, Any]) -> "ToolOutput":
		"""English: Return a copy with merged metadata. 中文：返回合并 metadata 的副本。"""
		return ToolOutput(
			content=deepcopy(self.content),
			content_type=self.content_type,
			summary=self.summary,
			llm_view=self.llm_view,
			artifacts=[ArtifactRef.from_dict(artifact.to_dict()) for artifact in self.artifacts],
			metadata={**deepcopy(self.metadata), **deepcopy(metadata)},
			is_error=self.is_error,
		)

	def _content_as_str(self) -> str:
		if isinstance(self.content, str):
			return self.content
		return json.dumps(self.content, ensure_ascii=False, default=str)

	def to_dict(self) -> dict[str, Any]:
		return {
			"content": deepcopy(self.content),
			"content_type": self.content_type,
			"summary": self.summary,
			"llm_view": self.llm_view,
			"artifacts": [a.to_dict() for a in self.artifacts],
			"metadata": deepcopy(self.metadata),
			"is_error": self.is_error,
		}

	@classmethod
	def from_dict(cls, d: dict[str, Any]) -> "ToolOutput":
		return cls(
			content=deepcopy(d["content"]),
			content_type=d.get("content_type", "text"),
			summary=d.get("summary", ""),
			llm_view=d.get("llm_view", ""),
			artifacts=[ArtifactRef.from_dict(a) for a in d.get("artifacts", [])],
			metadata=deepcopy(d.get("metadata", {})),
			is_error=d.get("is_error", False),
		)

	@classmethod
	def text(cls, content: str, summary: str = "", llm_view: str = "") -> "ToolOutput":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
便捷创建 text ToolOutput。"""
		return cls(content=content, content_type="text", summary=summary, llm_view=llm_view)

	@classmethod
	def json_output(cls, content: dict | list, summary: str = "", llm_view: str = "") -> "ToolOutput":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
便捷创建 JSON ToolOutput。"""
		return cls(content=content, content_type="json", summary=summary, llm_view=llm_view)

	@classmethod
	def error(cls, message: str) -> "ToolOutput":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
便捷创建 error ToolOutput。"""
		return cls(content=message, content_type="error", is_error=True)


def generate_artifact_id() -> str:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
生成唯一 artifact ID。"""
	return uuid.uuid4().hex[:16]
