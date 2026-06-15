"""Input provider boundary for normalizing raw agent input.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class InputProviderResult:
	"""Normalized messages plus optional artifacts and metadata.
中文：此文档说明相关引擎组件的行为。"""

	messages: list[dict[str, Any]]
	artifacts: list[object] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)

	def __post_init__(self) -> None:
		self.messages = deepcopy(self.messages)
		self.artifacts = list(self.artifacts)
		self.metadata = deepcopy(self.metadata)


class InputProvider(Protocol):
	"""Pre-loop input normalizer for text, media, files, or other raw input.
中文：此文档说明相关引擎组件的行为。"""

	async def process(self, messages: list[dict[str, Any]], context: dict[str, Any]) -> InputProviderResult: ...


class PassthroughInputProvider:
	"""Default input provider that preserves message content.
中文：此文档说明相关引擎组件的行为。"""

	async def process(self, messages: list[dict[str, Any]], context: dict[str, Any]) -> InputProviderResult:
		return InputProviderResult(messages=deepcopy(messages))
