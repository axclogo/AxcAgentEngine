"""Graph service layer independent of plugin lifecycle.
中文：此文档说明相关引擎组件的行为。"""
import json
from typing import Any

from axc_agent_engine.plugins.builtin.common import bounded_int
from axc_agent_engine.plugins.builtin.graph.config import GraphConfig
from axc_agent_engine.plugins.builtin.graph.policy import GraphPolicy
from axc_agent_engine.plugins.builtin.graph.source_loader import GraphSourceLoader
from axc_agent_engine.plugins.builtin.graph.utils import clean_text, filter_metadata, metadata


class GraphService:
	def __init__(self, config: GraphConfig, store: Any | None = None) -> None:
		self.config = config
		if store is None:
			raise ValueError("graph.store resource is required")
		self.store = store
		self.load_errors: list[dict[str, Any]] = []
		self.source_stats: dict[str, Any] = {"entities": 0, "relations": 0, "sources": len(config.sources)}
		self.policy = GraphPolicy(
			set(config.allowed_entity_types),
			set(config.denied_entity_types),
			set(config.allowed_relation_types),
			set(config.denied_relation_types),
		)
		self._source_loader = self._build_source_loader()
		self.load_sources()

	def inject_context(self, topic: str) -> str:
		if not self.config.enabled or not topic:
			return ""
		relevant = self.store.search(topic, depth=1, limit=5)
		if not relevant:
			return ""
		lines = ["[相关实体与关系]"]
		for row in relevant:
			entity = row.entity
			lines.append(f"- {entity['name']} ({entity.get('type', '')}): {entity.get('description', '')}")
			for rel in row.relations[:3]:
				target_name = rel.get("target_name") or rel.get("source_name") or rel.get("target_id", "")
				lines.append(f"  -> {rel['relation_type']} -> {target_name}")
		return "\n".join(lines)

	def search(self, query: str, depth_value: Any, limit_value: Any) -> tuple[dict[str, Any], int]:
		depth = bounded_int(depth_value, 0, self.config.max_depth)
		limit = self.limit(limit_value)
		rows = self.store.search(query, depth=depth, limit=limit)
		results = [{
			"entity": filter_metadata(row.entity, self.config.include_metadata),
			"relations": [filter_metadata(r, self.config.include_metadata) for r in row.relations],
		} for row in rows]
		return {"results": results, "count": len(results), "depth": depth}, limit

	def upsert_entity(self, args: dict[str, Any]) -> dict[str, Any]:
		name = clean_text(args.get("name", ""), self.config.max_name_length).strip()
		entity_type = clean_text(args.get("entity_type", "concept"), self.config.max_name_length) or "concept"
		description = clean_text(args.get("description", ""), self.config.max_description_length)
		aliases = [clean_text(alias, self.config.max_name_length) for alias in args.get("aliases", []) if str(alias).strip()]
		meta = metadata(args.get("metadata", {}), self.config.namespace)
		entity = self.store.upsert_entity(name, entity_type, aliases, description=description, **meta)
		return {"entity": entity, "name": name, "type": entity_type}

	def upsert_relation(self, args: dict[str, Any]) -> dict[str, Any]:
		source = clean_text(args.get("source", ""), self.config.max_name_length).strip()
		target = clean_text(args.get("target", ""), self.config.max_name_length).strip()
		relation_type = clean_text(args.get("relation_type", "RELATED_TO"), self.config.max_name_length) or "RELATED_TO"
		description = clean_text(args.get("description", ""), self.config.max_description_length)
		meta = metadata(args.get("metadata", {}), self.config.namespace)
		relation = self.store.upsert_relation(source, target, relation_type, description, **meta)
		return {"relation": relation, "source": source, "target": target, "relation_type": relation_type}

	def get_entity(self, entity_id: str) -> dict[str, Any] | None:
		entity = self.store.get_entity(entity_id)
		return filter_metadata(entity, self.config.include_metadata) if entity else None

	def get_relation(self, relation_id: str) -> dict[str, Any] | None:
		relation = self.store.get_relation(relation_id)
		return filter_metadata(relation, self.config.include_metadata) if relation else None

	def list_entities(self, args: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
		limit, offset = self.page(args)
		entities = [filter_metadata(entity, self.config.include_metadata) for entity in self.store.list_entities(limit=limit, offset=offset)]
		return entities, limit, offset

	def list_relations(self, args: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
		limit, offset = self.page(args)
		entity_id = args.get("entity_id") or None
		relations = [filter_metadata(relation, self.config.include_metadata) for relation in self.store.list_relations(entity_id=entity_id, limit=limit, offset=offset)]
		return relations, limit, offset

	def delete_entity(self, entity_id: str) -> bool:
		return self.store.delete_entity(entity_id)

	def delete_relation(self, relation_id: str) -> bool:
		return self.store.delete_relation(relation_id)

	def reload_sources(self, clear_existing: bool = False) -> None:
		if clear_existing:
			raise ValueError("graph reload cannot replace mounted graph.store")
		self.load_sources()

	def export_payload(self) -> dict[str, Any]:
		import time
		return {
			"namespace": self.config.namespace,
			"entities": self.store.list_entities(limit=self.config.max_entities, offset=0),
			"relations": self.store.list_relations(limit=self.config.max_relations, offset=0),
			"exported_at": time.time(),
		}

	def status_payload(self) -> dict[str, Any]:
		return {
			"enabled": self.config.enabled,
			"namespace": self.config.namespace,
			"entities": self.entity_count(),
			"relations": self.relation_count(),
			"sources": list(self.config.sources),
			"source_stats": dict(self.source_stats),
			"load_errors": list(self.load_errors),
			"config": {
				"allow_writes": self.config.allow_writes,
				"allow_deletes": self.config.allow_deletes,
				"max_entities": self.config.max_entities,
				"max_relations": self.config.max_relations,
				"max_depth": self.config.max_depth,
				"default_limit": self.config.default_limit,
				"max_limit": self.config.max_limit,
				"allowed_entity_types": sorted(self.config.allowed_entity_types),
				"denied_entity_types": sorted(self.config.denied_entity_types),
				"allowed_relation_types": sorted(self.config.allowed_relation_types),
				"denied_relation_types": sorted(self.config.denied_relation_types),
			},
		}

	def load_sources(self) -> None:
		self.source_stats = self._source_loader.load()

	def read_source(self, source: str) -> tuple[list[dict], list[dict]]:
		entities: list[dict] = []
		relations: list[dict] = []
		with open(source, "r", encoding="utf-8") as f:
			for line_no, line in enumerate(f, start=1):
				line = line.strip()
				if not line:
					continue
				try:
					item = json.loads(line)
				except json.JSONDecodeError as exc:
					error = {"source": source, "line": line_no, "error": str(exc)}
					self.load_errors.append(error)
					raise ValueError(str(error)) from exc
				if "source_id" in item and "target_id" in item and "relation_type" in item:
					relations.append(item)
				elif "source" in item and "target" in item and "relation_type" in item:
					relations.append(item)
				elif "id" in item and "name" in item:
					entities.append(item)
				else:
					error = {"source": source, "line": line_no, "error": "unknown graph record"}
					self.load_errors.append(error)
					raise ValueError(str(error))
		return entities, relations

	def validate_entity_type(self, entity_type: str) -> str:
		return self.policy.validate_entity_type(entity_type)

	def validate_relation_type(self, relation_type: str) -> str:
		return self.policy.validate_relation_type(relation_type)

	def limit(self, value: Any) -> int:
		return bounded_int(value, 1, self.config.max_limit)

	def page(self, args: dict[str, Any]) -> tuple[int, int]:
		return self.limit(args.get("limit", self.config.default_limit)), bounded_int(args.get("offset", 0), 0, 10_000_000)

	def entity_count(self) -> int:
		return len(getattr(self.store, "entities", {}))

	def relation_count(self) -> int:
		return len(getattr(self.store, "relations", {}))

	def metadata_payload(self, action: str) -> dict[str, Any]:
		return {
			"namespace": self.config.namespace,
			"last_action": action,
			"entities": self.entity_count(),
			"relations": self.relation_count(),
			"load_errors": len(self.load_errors),
		}

	def _build_source_loader(self) -> GraphSourceLoader:
		return GraphSourceLoader(
			self.config,
			self.store,
			self.policy,
			self.load_errors,
			self.read_source,
			self.entity_count,
			self.relation_count,
		)
