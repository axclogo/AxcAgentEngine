"""Small data models for context management.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextSummary:
	"""Structured summary state for older conversation history.
中文：此文档说明相关引擎组件的行为。"""

	content: str = ""
	failures: int = 0
	broken: bool = False


@dataclass
class RecallItem:
	"""Candidate recalled history snippet.
中文：此文档说明相关引擎组件的行为。"""

	text: str
	score: float
	message: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)
	compressed: bool = False


@dataclass
class PackedContext:
	"""Messages selected for a single LLM call.
中文：此文档说明相关引擎组件的行为。"""

	messages: list[dict[str, Any]]
	estimated_tokens: int
	truncated: bool = False
