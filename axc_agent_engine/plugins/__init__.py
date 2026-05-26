"""English: This documentation describes the related engine component behavior.
中文：插件系统。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.agent import Agent
	from axc_agent_engine.core.dispatcher import AgentMessageDispatcher
	from axc_agent_engine.llm.provider import LLMProvider
	from axc_agent_engine.storage.protocols import KVStore, MessagePersistence, SpanStore, MessageBus, ResultStore
	from axc_agent_engine.tools.registry import ToolRegistry

from axc_agent_engine.runtime.resources import ResourceRegistry


@dataclass(frozen=True)
class ModelInfo:
	"""Model names visible to plugins at initialize/runtime.
中文：此文档说明相关引擎组件的行为。"""
	default: str = ""
	fallback: str = ""
	utility: str = ""
	active: str = ""

	def to_dict(self) -> dict[str, str]:
		return asdict(self)


@dataclass(frozen=True)
class AgentInfo:
	"""Agent metadata visible to plugins at initialize/runtime.
中文：此文档说明相关引擎组件的行为。"""
	name: str = ""
	description: str = ""
	workspace: str = ""
	session_id: str = ""
	routing_mode: str = ""

	def to_dict(self) -> dict[str, str]:
		return asdict(self)


@dataclass
class PluginContext:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
插件初始化阶段可访问的 Engine 上下文。"""
	default_llm: "LLMProvider | None" = None
	fallback_llm: "LLMProvider | None" = None
	utility_llm: "LLMProvider | None" = None
	tool_registry: "ToolRegistry | None" = None
	kv_store: "KVStore | None" = None
	message_persistence: "MessagePersistence | None" = None
	span_store: "SpanStore | None" = None
	message_bus: "MessageBus | None" = None
	result_store: "ResultStore | None" = None
	resources: ResourceRegistry = field(default_factory=ResourceRegistry)
	dispatcher: "AgentMessageDispatcher | None" = None
	workspace: str = ""
	agent_getter: Callable[[str], "Agent | None"] | None = None
	agent_lister: Callable[[], "list[Agent]"] | None = None
	model_info: ModelInfo = field(default_factory=ModelInfo)
	agent_info: AgentInfo = field(default_factory=AgentInfo)

	@property
	def model_name(self) -> str:
		"""Primary model name for plugin initialization-time decisions.
中文：此文档说明相关引擎组件的行为。"""
		return self.model_info.default

	@property
	def agent_name(self) -> str:
		"""Current agent name for plugin initialization-time decisions.
中文：此文档说明相关引擎组件的行为。"""
		return self.agent_info.name

	def get_agent(self, name: str) -> "Agent | None":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
按名称获取 Agent。"""
		if self.agent_getter:
			return self.agent_getter(name)
		return None

	def list_agents(self) -> "list[Agent]":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
列出 engine 中所有已加载 Agent。"""
		if self.agent_lister:
			return self.agent_lister()
		return []


def model_info_from_providers(default_llm: object | None, fallback_llm: object | None = None,
							  utility_llm: object | None = None, active_llm: object | None = None) -> ModelInfo:
	"""Build plugin-facing model metadata from provider-like objects.
中文：此文档说明相关引擎组件的行为。"""
	default = _provider_model_name(default_llm)
	fallback = _provider_model_name(fallback_llm)
	utility = _provider_model_name(utility_llm)
	active = _provider_model_name(active_llm) or default
	return ModelInfo(default=default, fallback=fallback, utility=utility, active=active)


def _provider_model_name(provider: object | None) -> str:
	if provider is None:
		return ""
	model = getattr(provider, "model", "")
	if callable(model):
		try:
			model = model()
		except TypeError:
			model = ""
	return str(model or "")


def agent_info_from_runtime(name: str = "", description: str = "", workspace: str = "",
							session_id: str = "", routing_mode: str = "") -> AgentInfo:
	"""Build plugin-facing agent metadata.
中文：此文档说明相关引擎组件的行为。"""
	return AgentInfo(
		name=str(name or ""),
		description=str(description or ""),
		workspace=str(workspace or ""),
		session_id=str(session_id or ""),
		routing_mode=str(routing_mode or ""),
	)
