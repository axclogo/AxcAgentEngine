"""Small data models for context management. 上下文管理的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextSummary:
	"""Structured summary state for older conversation history. 较早会话历史的结构化摘要。"""

	content: str = ""
	failures: int = 0
	broken: bool = False


@dataclass
class RecallItem:
	"""Candidate recalled history snippet. 候选历史召回片段。"""

	text: str
	score: float
	message: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)
	compressed: bool = False


@dataclass
class PackedContext:
	"""Messages selected for a single LLM call. 为一次模型调用选取的消息。"""

	messages: list[dict[str, Any]]
	estimated_tokens: int
