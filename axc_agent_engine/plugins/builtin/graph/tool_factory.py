"""Graph tool schema factory.
中文：此文档说明相关引擎组件的行为。"""
from typing import Any

from axc_agent_engine.core.schema import ToolDefinition

from .config import GraphConfig
from .utils import graph_tool


class GraphToolFactory:
	def __init__(self, config: GraphConfig, handlers: dict[str, Any]) -> None:
		self._config = config
		self._handlers = handlers

	def tools(self) -> list[ToolDefinition]:
		if not self._config.enabled:
			return []
		return [
			graph_tool("graph_search", "搜索知识图谱中的实体和关系", {
				"query": {"type": "string", "description": "搜索关键词"},
				"depth": {"type": "integer", "description": "关系展开深度", "default": 1},
				"limit": {"type": "integer", "description": "返回实体数量", "default": self._config.default_limit},
			}, ["query"], True, "graph_read", "safe", self._handlers["graph_search"]),
			graph_tool("graph_upsert_entity", "新增或更新图谱实体", {
				"name": {"type": "string", "description": "实体名称"},
				"entity_type": {"type": "string", "description": "实体类型", "default": "concept"},
				"description": {"type": "string", "description": "实体描述", "default": ""},
				"aliases": {"type": "array", "items": {"type": "string"}, "description": "别名列表"},
				"metadata": {"type": "object", "description": "可选元数据", "default": {}},
			}, ["name"], False, "graph_write", "moderate", self._handlers["graph_upsert_entity"]),
			graph_tool("graph_upsert_relation", "新增或更新实体关系", {
				"source": {"type": "string", "description": "源实体名称"},
				"target": {"type": "string", "description": "目标实体名称"},
				"relation_type": {"type": "string", "description": "关系类型", "default": "RELATED_TO"},
				"description": {"type": "string", "description": "关系描述", "default": ""},
				"metadata": {"type": "object", "description": "可选元数据", "default": {}},
			}, ["source", "target"], False, "graph_write", "moderate", self._handlers["graph_upsert_relation"]),
			graph_tool("graph_get_entity", "按 ID 获取图谱实体", {"entity_id": {"type": "string", "description": "实体 ID"}}, ["entity_id"], True, "graph_read", "safe", self._handlers["graph_get_entity"]),
			graph_tool("graph_get_relation", "按 ID 获取图谱关系", {"relation_id": {"type": "string", "description": "关系 ID"}}, ["relation_id"], True, "graph_read", "safe", self._handlers["graph_get_relation"]),
			graph_tool("graph_list_entities", "列出图谱实体", {
				"limit": {"type": "integer", "default": self._config.default_limit},
				"offset": {"type": "integer", "default": 0},
			}, [], True, "graph_read", "safe", self._handlers["graph_list_entities"]),
			graph_tool("graph_delete_entity", "删除图谱实体及其关联关系", {"entity_id": {"type": "string", "description": "实体 ID"}}, ["entity_id"], False, "graph_delete", "dangerous", self._handlers["graph_delete_entity"]),
			graph_tool("graph_list_relations", "列出图谱关系", {
				"entity_id": {"type": "string", "description": "可选实体 ID"},
				"limit": {"type": "integer", "default": self._config.default_limit},
				"offset": {"type": "integer", "default": 0},
			}, [], True, "graph_read", "safe", self._handlers["graph_list_relations"]),
			graph_tool("graph_delete_relation", "删除图谱关系", {"relation_id": {"type": "string", "description": "关系 ID"}}, ["relation_id"], False, "graph_delete", "dangerous", self._handlers["graph_delete_relation"]),
			graph_tool("graph_status", "查看图谱插件状态、配置、实体/关系数量和源加载错误", {}, [], True, "graph_read", "safe", self._handlers["graph_status"]),
			graph_tool("graph_reload_sources", "重新加载配置的数据源", {"clear_existing": {"type": "boolean", "default": False}}, [], False, "graph_write", "moderate", self._handlers["graph_reload_sources"]),
			graph_tool("graph_export", "导出当前图谱实体和关系", {}, [], True, "graph_read", "safe", self._handlers["graph_export"]),
		]
