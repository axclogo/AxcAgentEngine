"""Graph 插件 — 实体关系图谱检索、治理和 CRUD。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import GRAPH_CONFIG_SCHEMA
from axc_agent_engine.plugins.builtin.graph.audit import GraphAuditRecorder
from axc_agent_engine.plugins.builtin.graph.config import GraphConfig
from axc_agent_engine.plugins.builtin.graph.presenter import GraphPresenter
from axc_agent_engine.plugins.builtin.graph.service import GraphService
from axc_agent_engine.plugins.builtin.graph.tool_factory import GraphToolFactory
from axc_agent_engine.plugins.builtin.graph.tool_handlers import GraphToolHandlers

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext


class GraphPlugin(BasePlugin):
	name = "graph"
	display_name = "知识图谱"
	priority = 20
	version = "2.0.0"
	config_schema = GRAPH_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		super().initialize(config, plugin_ctx)
		self._config = GraphConfig.from_dict(config)
		store_resource = "graph.store"
		store = plugin_ctx.resources.get(store_resource)
		if store is None:
			raise ValueError("graph plugin requires mounts['graph.store']")
		self._service = GraphService(self._config, store)
		self._presenter = GraphPresenter(self._config, plugin_ctx)
		self._audit_recorder = GraphAuditRecorder(self._config)
		self._handlers = GraphToolHandlers(self._service, self._presenter, self._audit_recorder)
		self._tool_factory = GraphToolFactory(self._config, self._handlers.tools())

		self._store = self._service.store
		self._load_errors = self._service.load_errors
		self._source_stats = self._service.source_stats

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str = "") -> str:
		return self._service.inject_context(topic)

	def get_tools(self) -> list[ToolDefinition]:
		return self._tool_factory.tools()

	async def _tool_graph_search(self, args: dict, context: dict):
		return await self._handlers.graph_search(args, context)

	async def _tool_upsert_entity(self, args: dict, context: dict):
		return await self._handlers.upsert_entity(args, context)

	async def _tool_upsert_relation(self, args: dict, context: dict):
		return await self._handlers.upsert_relation(args, context)

	async def _tool_get_entity(self, args: dict, context: dict):
		return await self._handlers.get_entity(args, context)

	async def _tool_get_relation(self, args: dict, context: dict):
		return await self._handlers.get_relation(args, context)

	async def _tool_list_entities(self, args: dict, context: dict):
		return await self._handlers.list_entities(args, context)

	async def _tool_delete_entity(self, args: dict, context: dict):
		return await self._handlers.delete_entity(args, context)

	async def _tool_list_relations(self, args: dict, context: dict):
		return await self._handlers.list_relations(args, context)

	async def _tool_delete_relation(self, args: dict, context: dict):
		return await self._handlers.delete_relation(args, context)

	async def _tool_status(self, args: dict, context: dict):
		return await self._handlers.status(args, context)

	async def _tool_reload_sources(self, args: dict, context: dict):
		result = await self._handlers.reload_sources(args, context)
		self._store = self._service.store
		self._load_errors = self._service.load_errors
		self._source_stats = self._service.source_stats
		return result

	async def _tool_export(self, args: dict, context: dict):
		return await self._handlers.export(args, context)

	def _load_sources(self) -> None:
		self._service.load_sources()
		self._source_stats = self._service.source_stats

	def _read_source(self, source: str) -> tuple[list[dict], list[dict]]:
		return self._service.read_source(source)

	def _validate_entity_type(self, entity_type: str) -> str:
		return self._service.validate_entity_type(entity_type)

	def _validate_relation_type(self, relation_type: str) -> str:
		return self._service.validate_relation_type(relation_type)

	def _limit(self, value: Any) -> int:
		return self._service.limit(value)

	def _page(self, args: dict) -> tuple[int, int]:
		return self._service.page(args)

	def _entity_count(self) -> int:
		return self._service.entity_count()

	def _relation_count(self) -> int:
		return self._service.relation_count()

	def _status_payload(self) -> dict[str, Any]:
		return self._service.status_payload()
