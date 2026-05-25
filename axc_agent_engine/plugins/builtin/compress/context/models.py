"""Small data models for context management."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextSummary:
	"""Structured summary state for older conversation history."""

	content: str = ""
	failures: int = 0
	broken: bool = False


@dataclass
class RecallItem:
	"""Candidate recalled history snippet."""

	text: str
	score: float
	message: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)
	compressed: bool = False


@dataclass
class PackedContext:
	"""Messages selected for a single LLM call."""

	messages: list[dict[str, Any]]
	estimated_tokens: int
	truncated: bool = False
