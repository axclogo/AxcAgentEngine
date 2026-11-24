"""Per-agent tool registry.
工具注册表 — Agent 级"""

import logging
import threading
import time
from typing import Any

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.tools.name_mapping import ToolNameMapper, ToolNameMappingConfig

logger = logging.getLogger(__name__)


class ToolRegistrationResolver:
	"""Normalizes tool registration input and model-facing names. 规范化工具注册输入和模型可见名称。"""

	def __init__(self, name_mapping: ToolNameMappingConfig | ToolNameMapper | None = None) -> None:
		self._name_mapper = name_mapping if isinstance(name_mapping, ToolNameMapper) else ToolNameMapper(name_mapping)

	def normalize(self, tool: ToolDefinition) -> ToolDefinition:
		if not isinstance(tool, ToolDefinition):
			raise TypeError("ToolRegistry only accepts ToolDefinition instances")
		return tool

	def resolve_name(self, name: str) -> str:
		return self._name_mapper.decode(name)

	def model_name(self, name: str) -> str:
		return self._name_mapper.encode(name)

	def clear(self) -> None:
		self._name_mapper.clear()


class ToolSchemaPresenter:
	"""Builds provider-facing tool schemas from internal definitions. 从内部定义生成模型供应方使用的工具参数结构。"""

	def __init__(self, resolver: ToolRegistrationResolver) -> None:
		self._resolver = resolver

	def openai_schemas(self, tools: dict[str, ToolDefinition]) -> list[dict[str, Any]]:
		schemas = []
		for tool in sorted(tools.values(), key=lambda t: t.name):
			if tool.deferred:
				continue
			schema = tool.to_openai_schema()
			schema["function"]["name"] = self._resolver.model_name(tool.name)
			schemas.append(schema)
		return schemas


class ToolRegistry:
	"""Agent 级工具注册表。"""

	def __init__(self, name_mapping: ToolNameMappingConfig | ToolNameMapper | None = None) -> None:
		self._tools: dict[str, ToolDefinition] = {}
		self._resolver = ToolRegistrationResolver(name_mapping)
		self._schema_presenter = ToolSchemaPresenter(self._resolver)
		# Registry initialization uses a synchronous lock. 中文：这里刻意使用 threading.Lock：注册只发生在初始化阶段，不在 async 上下文中，
		# This avoids blocking the event loop. 因此不会阻塞事件循环。
		self._lock = threading.Lock()
		self._frozen = False
		self._schema_version = 0
		self._registration_log: list[dict[str, Any]] = []

	def freeze(self) -> None:
		"""Freeze registration while permitting explicit late registration.
		初始化后锁定注册表；仍允许通过 register_late() 后注册。"""
		self._frozen = True

	def register(self, tool: ToolDefinition) -> None:
		"""Register a tool. 注册工具。"""
		tool = self._resolver.normalize(tool)
		if self._frozen:
			raise RuntimeError(f"ToolRegistry is frozen, cannot register '{tool.name}'")
		self._do_register(tool)

	def register_late(
		self,
		tool: ToolDefinition,
		plugin_name: str = "",
		reason: str = "late_registration",
	) -> None:
		"""freeze 后注册工具，用于 MCP 这类异步初始化插件。

		动态注册会更新 schema_version 并记录来源，便于审计和 checkpoint 排查。
		"""
		self._do_register(tool, source=plugin_name or "unknown", reason=reason)

	def register_late_many(
		self,
		tools: list[ToolDefinition],
		plugin_name: str = "",
		reason: str = "late_registration",
	) -> None:
		"""Register a batch after the initial registration phase. 批量后注册。"""
		for t in tools:
			self.register_late(t, plugin_name=plugin_name, reason=reason)

	def _do_register(
		self,
		tool: ToolDefinition,
		source: str = "initial",
		reason: str = "register",
	) -> None:
		"""Internal tool registration. 内部注册逻辑。"""
		tool = self._resolver.normalize(tool)
		if not tool.name:
			logger.warning("Tool definition missing 'name' field, skipping")
			return
		with self._lock:
			self._tools[tool.name] = tool
			self._resolver.model_name(tool.name)
			self._schema_version += 1
			self._registration_log.append(
				{
					"name": tool.name,
					"source": source,
					"reason": reason,
					"version": self._schema_version,
					"timestamp": time.time(),
					"frozen": self._frozen,
				}
			)

	def register_many(self, tools: list[ToolDefinition]) -> None:
		"""Register tools in a batch. 批量注册。"""
		for t in tools:
			self.register(t)

	def get(self, name: str) -> ToolDefinition | None:
		return self._tools.get(self.resolve_name(name))

	def resolve_name(self, name: str) -> str:
		"""Resolve a model-facing alias back to the internal tool name. 将模型可用的别名还原为内部工具名。"""
		return self._resolver.resolve_name(name)

	def model_name(self, name: str) -> str:
		"""Return the model-facing alias for an internal tool name. 从内部工具名获取模型可用的别名。"""
		return self._resolver.model_name(name)

	def get_all(self) -> list[ToolDefinition]:
		return list(self._tools.values())

	def get_openai_schemas(self) -> list[dict[str, Any]]:
		"""Return function schemas ordered by name.
		返回 OpenAI function calling 格式（按 name 排序，利于 prompt cache）
		                deferred 工具不包含在初始 schema 中，由 DeferredToolNode 动态注入"""
		return self._schema_presenter.openai_schemas(self._tools)

	def has(self, name: str) -> bool:
		return name in self._tools

	def clear(self) -> None:
		with self._lock:
			self._tools.clear()
			self._resolver.clear()
			self._schema_version = 0
			self._registration_log.clear()

	@property
	def count(self) -> int:
		return len(self._tools)

	@property
	def schema_version(self) -> int:
		return self._schema_version

	def registration_log(self) -> list[dict[str, Any]]:
		return [dict(item) for item in self._registration_log]
