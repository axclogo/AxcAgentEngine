"""Input provider boundary for normalizing raw agent input."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class InputProviderResult:
	"""Normalized messages plus optional artifacts and metadata."""

	messages: list[dict[str, Any]]
	artifacts: list[object] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)


class InputProvider(Protocol):
	"""Pre-loop input normalizer for text, media, files, or other raw input."""

	async def process(self, messages: list[dict[str, Any]], context: dict[str, Any]) -> InputProviderResult: ...


class PassthroughInputProvider:
	"""Default input provider that preserves message content."""

	async def process(self, messages: list[dict[str, Any]], context: dict[str, Any]) -> InputProviderResult:
		return InputProviderResult(messages=deepcopy(messages))
