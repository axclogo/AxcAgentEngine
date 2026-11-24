"""Input provider boundary for normalizing raw agent input. 将宿主输入转换为执行消息的接口。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class InputProviderResult:
	"""Normalized messages plus optional artifacts and metadata. 规范化消息及可选的产物和元数据。"""

	messages: list[dict[str, Any]]
	artifacts: list[object] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)

	def __post_init__(self) -> None:
		self.messages = deepcopy(self.messages)
		self.artifacts = list(self.artifacts)
		self.metadata = deepcopy(self.metadata)


class InputProvider(Protocol):
	"""Pre-loop input normalizer for text, media, files, or other raw input. 执行循环前将文本、媒体及文件规范化。"""

	async def process(self, messages: list[dict[str, Any]], context: dict[str, Any]) -> InputProviderResult: ...


class PassthroughInputProvider:
	"""Default input provider that preserves message content. 保留原始消息内容的默认输入提供器。"""

	async def process(self, messages: list[dict[str, Any]], context: dict[str, Any]) -> InputProviderResult:
		return InputProviderResult(messages=deepcopy(messages))
