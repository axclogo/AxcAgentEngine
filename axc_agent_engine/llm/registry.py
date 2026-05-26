"""ProviderRegistry — 命名 LLM provider 管理。

允许按名称注册 provider，并在每个 Agent 的配置里解析引用。
Engine.load_agent 支持 llm="coder" 这种命名 provider 引用。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.llm.provider import LLMProvider


class ProviderRegistry:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
命名 LLM provider 注册表。"""

	def __init__(self) -> None:
		self._providers: dict[str, "LLMProvider"] = {}

	def register(self, name: str, provider: "LLMProvider") -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
按名称注册 provider。"""
		self._providers[name] = provider

	def register_llm(self, name: str, provider: "LLMProvider") -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
按名称注册 LLM provider。"""
		self.register(name, provider)

	def get(self, name: str) -> "LLMProvider | None":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
按名称获取 provider；不存在时返回 None。"""
		return self._providers.get(name)

	def get_llm(self, name: str) -> "LLMProvider | None":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
按名称获取 LLM provider。"""
		return self.get(name)

	def resolve(self, ref: "str | LLMProvider | None") -> "LLMProvider | None":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
解析 provider 引用，可以是名称字符串或 provider 实例。"""
		if ref is None:
			return None
		if isinstance(ref, str):
			return self._providers.get(ref)
		return ref

	def list_names(self) -> list[str]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
列出所有已注册 provider 名称。"""
		return list(self._providers.keys())

	async def close_all(self) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
关闭所有已注册 provider。"""
		for provider in self._providers.values():
			await provider.close()
		self._providers.clear()

	@property
	def count(self) -> int:
		return len(self._providers)
