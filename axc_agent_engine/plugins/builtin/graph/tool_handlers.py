"""Graph tool argument handling and ToolOutput conversion."""
import time
from typing import Any

from axc_agent_engine.core.errors import ErrorCategory, ErrorEnvelope
from axc_agent_engine.plugins.builtin.common import exec_ctx_from_tool_context
from axc_agent_engine.plugins.builtin.graph.audit import GraphAuditRecorder
from axc_agent_engine.plugins.builtin.graph.presenter import GraphPresenter
from axc_agent_engine.plugins.builtin.graph.service import GraphService
from axc_agent_engine.plugins.builtin.graph.utils import filter_metadata


class GraphToolHandlers:
	def __init__(self, service: GraphService, presenter: GraphPresenter, audit_recorder: GraphAuditRecorder) -> None:
		self._service = service
		self._presenter = presenter
		self._audit_recorder = audit_recorder

	def tools(self) -> dict[str, Any]:
		return {
			"graph_search": self.graph_search,
			"graph_upsert_entity": self.upsert_entity,
			"graph_upsert_relation": self.upsert_relation,
			"graph_get_entity": self.get_entity,
			"graph_get_relation": self.get_relation,
			"graph_list_entities": self.list_entities,
			"graph_delete_entity": self.delete_entity,
			"graph_list_relations": self.list_relations,
			"graph_delete_relation": self.delete_relation,
			"graph_status": self.status,
			"graph_reload_sources": self.reload_sources,
			"graph_export": self.export,
		}

	async def graph_search(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		started = time.time()
		query = str(args.get("query", "")).strip()
		if not query:
			return ToolOutput.error("query 不能为空")
		payload, _ = self._service.search(query, args.get("depth", 1), args.get("limit", self._service.config.default_limit))
		self.sync_metadata(exec_ctx_from_tool_context(context), "search")
		await self.audit(exec_ctx_from_tool_context(context), "graph_search", "graph_search", "graph_read", "safe", started, True, {"query": query[:100], "count": payload["count"], "depth": payload["depth"]})
		return await self._presenter.json_output(payload, context, f"graph_search：为 '{query[:50]}' 找到 {payload['count']} 个实体", "graph_search_result")

	async def upsert_entity(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		started = time.time()
		if not self._service.config.allow_writes:
			return await self.error("graph writes disabled", context, "graph_upsert_entity", "graph_write", "moderate", started, "graph.write_disabled")
		if self._service.entity_count() >= self._service.config.max_entities:
			return await self.error("实体数量超过限制", context, "graph_upsert_entity", "graph_write", "moderate", started, "graph.entity_limit")
		prepared = self._service.upsert_entity(args)
		type_error = self._service.validate_entity_type(prepared["type"])
		if type_error:
			return await self.error(type_error, context, "graph_upsert_entity", "graph_write", "moderate", started, "graph.entity_type_denied")
		if not prepared["name"]:
			return ToolOutput.error("name 不能为空")
		entity = prepared["entity"]
		self.sync_metadata(exec_ctx_from_tool_context(context), "upsert_entity")
		await self.audit(exec_ctx_from_tool_context(context), "graph_entity_upserted", "graph_upsert_entity", "graph_write", "moderate", started, True, {"entity_id": entity["id"], "name": prepared["name"], "type": prepared["type"]})
		return ToolOutput.json_output({"entity": filter_metadata(entity, self._service.config.include_metadata)}, summary=f"已更新实体：{prepared['name']}")

	async def upsert_relation(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		started = time.time()
		if not self._service.config.allow_writes:
			return await self.error("graph writes disabled", context, "graph_upsert_relation", "graph_write", "moderate", started, "graph.write_disabled")
		if self._service.relation_count() >= self._service.config.max_relations:
			return await self.error("关系数量超过限制", context, "graph_upsert_relation", "graph_write", "moderate", started, "graph.relation_limit")
		prepared = self._service.upsert_relation(args)
		type_error = self._service.validate_relation_type(prepared["relation_type"])
		if type_error:
			return await self.error(type_error, context, "graph_upsert_relation", "graph_write", "moderate", started, "graph.relation_type_denied")
		if not prepared["source"] or not prepared["target"]:
			return ToolOutput.error("source 和 target 不能为空")
		relation = prepared["relation"]
		self.sync_metadata(exec_ctx_from_tool_context(context), "upsert_relation")
		await self.audit(exec_ctx_from_tool_context(context), "graph_relation_upserted", "graph_upsert_relation", "graph_write", "moderate", started, True, {"relation_id": relation["id"], "source": prepared["source"], "target": prepared["target"], "relation_type": prepared["relation_type"]})
		return ToolOutput.json_output({"relation": filter_metadata(relation, self._service.config.include_metadata)}, summary=f"已更新关系：{prepared['source']} -> {prepared['target']}")

	async def get_entity(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		entity = self._service.get_entity(str(args.get("entity_id", "")))
		self.sync_metadata(exec_ctx_from_tool_context(context), "get_entity")
		if not entity:
			return ToolOutput.error("entity not found")
		return ToolOutput.json_output({"entity": entity}, summary=f"已获取实体：{entity['name']}")

	async def get_relation(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		relation = self._service.get_relation(str(args.get("relation_id", "")))
		self.sync_metadata(exec_ctx_from_tool_context(context), "get_relation")
		if not relation:
			return ToolOutput.error("relation not found")
		return ToolOutput.json_output({"relation": relation}, summary=f"已获取关系：{relation['id']}")

	async def list_entities(self, args: dict, context: dict):
		entities, limit, offset = self._service.list_entities(args)
		self.sync_metadata(exec_ctx_from_tool_context(context), "list_entities")
		return await self._presenter.json_output({"entities": entities, "count": len(entities), "limit": limit, "offset": offset}, context, f"已列出 {len(entities)} 个实体", "graph_entities")

	async def delete_entity(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		started = time.time()
		if not self._service.config.allow_deletes:
			return await self.error("graph deletes disabled", context, "graph_delete_entity", "graph_delete", "dangerous", started, "graph.delete_disabled")
		entity_id = str(args.get("entity_id", ""))
		deleted = self._service.delete_entity(entity_id)
		self.sync_metadata(exec_ctx_from_tool_context(context), "delete_entity")
		await self.audit(exec_ctx_from_tool_context(context), "graph_entity_deleted", "graph_delete_entity", "graph_delete", "dangerous", started, deleted, {"entity_id": entity_id})
		return ToolOutput.json_output({"deleted": deleted}, summary="实体已删除" if deleted else "实体不存在")

	async def list_relations(self, args: dict, context: dict):
		relations, limit, offset = self._service.list_relations(args)
		self.sync_metadata(exec_ctx_from_tool_context(context), "list_relations")
		return await self._presenter.json_output({"relations": relations, "count": len(relations), "limit": limit, "offset": offset}, context, f"已列出 {len(relations)} 条关系", "graph_relations")

	async def delete_relation(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		started = time.time()
		if not self._service.config.allow_deletes:
			return await self.error("graph deletes disabled", context, "graph_delete_relation", "graph_delete", "dangerous", started, "graph.delete_disabled")
		relation_id = str(args.get("relation_id", ""))
		deleted = self._service.delete_relation(relation_id)
		self.sync_metadata(exec_ctx_from_tool_context(context), "delete_relation")
		await self.audit(exec_ctx_from_tool_context(context), "graph_relation_deleted", "graph_delete_relation", "graph_delete", "dangerous", started, deleted, {"relation_id": relation_id})
		return ToolOutput.json_output({"deleted": deleted}, summary="关系已删除" if deleted else "关系不存在")

	async def status(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		self.sync_metadata(exec_ctx_from_tool_context(context), "status")
		return ToolOutput.json_output(self._service.status_payload(), summary=f"graph：{self._service.entity_count()} entities, {self._service.relation_count()} relations")

	async def reload_sources(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		started = time.time()
		if not self._service.config.allow_writes:
			return await self.error("graph writes disabled", context, "graph_reload_sources", "graph_write", "moderate", started, "graph.write_disabled")
		self._service.reload_sources(clear_existing=bool(args.get("clear_existing", False)))
		self.sync_metadata(exec_ctx_from_tool_context(context), "reload_sources")
		await self.audit(exec_ctx_from_tool_context(context), "graph_sources_reloaded", "graph_reload_sources", "graph_write", "moderate", started, True, self._service.source_stats)
		return ToolOutput.json_output(self._service.status_payload(), summary=f"已重新加载 {self._service.source_stats['sources']} 个 graph source")

	async def export(self, args: dict, context: dict):
		payload = self._service.export_payload()
		self.sync_metadata(exec_ctx_from_tool_context(context), "export")
		return await self._presenter.json_output(payload, context, "已导出 graph", "graph_export")

	def sync_metadata(self, exec_ctx, action: str) -> None:
		if exec_ctx:
			exec_ctx.state.metadata["graph"] = self._service.metadata_payload(action)

	async def audit(self, exec_ctx, event_type: str, tool_name: str,
					capability: str, risk_level: str, started: float, allowed: bool,
					metadata: dict[str, Any], error: ErrorEnvelope | None = None) -> None:
		await self._audit_recorder.record(exec_ctx, event_type, tool_name, capability, risk_level, started, allowed, metadata, error)

	async def error(self, message: str, context: dict, tool_name: str, capability: str,
					risk_level: str, started: float, code: str):
		from axc_agent_engine.tools.tool_output import ToolOutput
		error = ErrorEnvelope(code=code, message=message, category=ErrorCategory.TOOL)
		await self.audit(exec_ctx_from_tool_context(context), f"{tool_name}_rejected", tool_name, capability, risk_level, started, False, {}, error)
		return ToolOutput.error(message)
