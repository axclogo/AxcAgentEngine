"""Session — 独立会话管理"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
	"""English: This documentation describes the related engine component behavior.
中文：单个会话，持有消息历史"""
	session_id: str
	messages: list[dict[str, Any]] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)

	def add_message(self, role: str, content: str) -> None:
		self.messages.append({"role": role, "content": content})

	def clear(self) -> None:
		self.messages.clear()

	def to_dict(self) -> dict:
		return {"session_id": self.session_id, "messages": deepcopy(self.messages), "metadata": deepcopy(self.metadata)}

	@classmethod
	def from_dict(cls, data: dict) -> "Session":
		return cls(session_id=data["session_id"], messages=deepcopy(data.get("messages", [])), metadata=deepcopy(data.get("metadata", {})))
