"""Tool name mapping for model-specific function-call constraints.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


class ToolNameMappingError(ValueError):
	"""Raised when a tool name cannot be mapped safely.
中文：此文档说明相关引擎组件的行为。"""


@dataclass(frozen=True)
class ToolNameMappingConfig:
	"""Rules exposed by LLM providers for model-facing tool names.
中文：此文档说明相关引擎组件的行为。"""

	pattern: str = r"^[a-zA-Z0-9_-]{1,64}$"
	replacement: str = "_"
	max_length: int = 64
	collision: str = "hash_suffix"
	case: str = "preserve"


class ToolNameMapper:
	"""Encode internal tool names to model-safe aliases and decode them back.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, config: ToolNameMappingConfig | None = None) -> None:
		self.config = config or ToolNameMappingConfig()
		self._encoded_to_real: dict[str, str] = {}
		self._real_to_encoded: dict[str, str] = {}
		self._pattern = re.compile(self.config.pattern)

	def encode(self, real_name: str) -> str:
		if real_name in self._real_to_encoded:
			return self._real_to_encoded[real_name]
		alias = sanitize_tool_name(real_name, self.config)
		alias = self._dedupe(real_name, alias)
		if not self._pattern.fullmatch(alias):
			raise ToolNameMappingError(f"Mapped tool name does not match pattern: {alias}")
		self._real_to_encoded[real_name] = alias
		self._encoded_to_real[alias] = real_name
		return alias

	def decode(self, model_name: str) -> str:
		return self._encoded_to_real.get(model_name, model_name)

	def clear(self) -> None:
		self._encoded_to_real.clear()
		self._real_to_encoded.clear()

	def _dedupe(self, real_name: str, alias: str) -> str:
		owner = self._encoded_to_real.get(alias)
		if owner in (None, real_name):
			return alias
		if self.config.collision != "hash_suffix":
			raise ToolNameMappingError(f"Tool name alias collision: {alias}")
		suffix = hashlib.sha1(real_name.encode("utf-8")).hexdigest()[:6]
		trimmed = alias[: max(1, self.config.max_length - len(suffix) - 1)].rstrip("_-")
		return f"{trimmed}_{suffix}"


def sanitize_tool_name(name: str, config: ToolNameMappingConfig | None = None) -> str:
	"""Return a model-safe name using a conservative character replacement rule.
中文：此文档说明相关引擎组件的行为。"""
	config = config or ToolNameMappingConfig()
	value = name.lower() if config.case == "lower" else name
	value = re.sub(r"[^a-zA-Z0-9_-]+", config.replacement, value)
	value = re.sub(re.escape(config.replacement) + r"+", config.replacement, value)
	value = value.strip(config.replacement) or "tool"
	if len(value) > config.max_length:
		value = value[: config.max_length].rstrip(config.replacement) or "tool"
	return value
