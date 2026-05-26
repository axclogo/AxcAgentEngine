"""General entity relationship graph store.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class GraphSearchResult:
	entity: dict[str, Any]
	relations: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class GraphStore(Protocol):
	"""Storage-neutral entity/relation graph API.
中文：此文档说明相关引擎组件的行为。"""
	def upsert_entity(self, name: str, entity_type: str = "concept", aliases: list[str] | None = None, **metadata: Any) -> dict[str, Any]: ...
	def get_entity(self, entity_id: str) -> dict[str, Any] | None: ...
	def list_entities(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]: ...
	def delete_entity(self, entity_id: str) -> bool: ...
	def upsert_relation(self, source: str, target: str, relation_type: str = "RELATED_TO", description: str = "", source_memory_id: str = "", **metadata: Any) -> dict[str, Any]: ...
	def get_relation(self, relation_id: str) -> dict[str, Any] | None: ...
	def list_relations(self, entity_id: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]: ...
	def delete_relation(self, relation_id: str) -> bool: ...
	def search(self, query: str, depth: int = 1, limit: int = 5) -> list[GraphSearchResult]: ...


class InMemoryGraphStore:
	"""Dependency-free graph store for engine default usage.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self) -> None:
		self.entities: dict[str, dict[str, Any]] = {}
		self.relations: dict[str, dict[str, Any]] = {}

	def upsert_entity(self, name: str, entity_type: str = "concept",
					  aliases: list[str] | None = None, **metadata: Any) -> dict[str, Any]:
		name = name.strip()
		if not name:
			raise ValueError("entity name cannot be empty")
		entity = self._resolve_entity(name, entity_type, aliases or [])
		if entity:
			entity["mention_count"] += 1
			entity["aliases"].update(aliases or [])
			if entity_type and entity.get("type") == "concept":
				entity["type"] = entity_type
			entity["metadata"].update(metadata)
			return self._entity_dict(entity["id"])
		entity_id = f"entity:{len(self.entities) + 1}"
		self.entities[entity_id] = {
			"id": entity_id,
			"name": name,
			"type": entity_type,
			"aliases": set(aliases or []),
			"mention_count": 1,
			"metadata": dict(metadata),
		}
		return self._entity_dict(entity_id)

	def upsert_relation(self, source: str, target: str, relation_type: str = "RELATED_TO",
						description: str = "", source_memory_id: str = "", **metadata: Any) -> dict[str, Any]:
		source_entity = self.upsert_entity(source)
		target_entity = self.upsert_entity(target)
		for relation in self.relations.values():
			if (relation["source_id"] == source_entity["id"]
					and relation["target_id"] == target_entity["id"]
					and relation["relation_type"] == relation_type):
				if len(description) > len(relation.get("description", "")):
					relation["description"] = description
				relation["confidence"] = min(float(relation.get("confidence", 0.8)) + 0.05, 1.0)
				relation["metadata"].update(metadata)
				return self._relation_dict(relation["id"])
		relation_id = f"relation:{len(self.relations) + 1}"
		self.relations[relation_id] = {
			"id": relation_id,
			"source_id": source_entity["id"],
			"target_id": target_entity["id"],
			"relation_type": relation_type,
			"description": description,
			"confidence": 0.8,
			"source_memory_id": source_memory_id,
			"metadata": dict(metadata),
		}
		return self._relation_dict(relation_id)

	def get_entity(self, entity_id: str) -> dict[str, Any] | None:
		if entity_id not in self.entities:
			return None
		return self._entity_dict(entity_id)

	def list_entities(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
		ids = list(self.entities.keys())
		return [self._entity_dict(entity_id) for entity_id in ids[offset:offset + limit]]

	def delete_entity(self, entity_id: str) -> bool:
		if entity_id not in self.entities:
			return False
		self.entities.pop(entity_id, None)
		for relation_id, relation in list(self.relations.items()):
			if relation["source_id"] == entity_id or relation["target_id"] == entity_id:
				self.relations.pop(relation_id, None)
		return True

	def get_relation(self, relation_id: str) -> dict[str, Any] | None:
		if relation_id not in self.relations:
			return None
		return self._relation_dict(relation_id)

	def list_relations(self, entity_id: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
		relations = []
		for relation_id, relation in self.relations.items():
			if entity_id and relation["source_id"] != entity_id and relation["target_id"] != entity_id:
				continue
			relations.append(self._relation_dict(relation_id))
		return relations[offset:offset + limit]

	def delete_relation(self, relation_id: str) -> bool:
		return self.relations.pop(relation_id, None) is not None

	def search_entities(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
		query_lower = query.lower()
		scored: list[tuple[float, str]] = []
		for entity_id, entity in self.entities.items():
			haystack = " ".join([entity["name"], entity["type"], *entity["aliases"], str(entity["metadata"])]).lower()
			score = 0.0
			if query_lower in entity["name"].lower():
				score += 2.0
			elif query_lower and query_lower in haystack:
				score += 1.0
			else:
				score += sum(0.3 for word in query_lower.split() if word and word in haystack)
			if score > 0:
				scored.append((score, entity_id))
		scored.sort(key=lambda item: item[0], reverse=True)
		return [self._entity_dict(entity_id) for _, entity_id in scored[:limit]]

	def relations_for(self, entity_id: str, limit: int = 10) -> list[dict[str, Any]]:
		results = []
		for relation_id, relation in self.relations.items():
			if relation["source_id"] == entity_id or relation["target_id"] == entity_id:
				results.append(self._relation_dict(relation_id))
		return results[:limit]

	def search(self, query: str, depth: int = 1, limit: int = 5) -> list[GraphSearchResult]:
		results = []
		for entity in self.search_entities(query, limit=limit):
			relations: list[dict[str, Any]] = []
			self._expand(entity["id"], depth, set(), relations)
			results.append(GraphSearchResult(entity=entity, relations=relations))
		return results

	def _expand(self, entity_id: str, depth: int, visited: set[str], output: list[dict[str, Any]]) -> None:
		if depth <= 0 or entity_id in visited:
			return
		visited.add(entity_id)
		for relation in self.relations_for(entity_id, limit=10):
			output.append(relation)
			next_id = relation["target_id"] if relation["source_id"] == entity_id else relation["source_id"]
			self._expand(next_id, depth - 1, visited, output)

	def _entity_dict(self, entity_id: str) -> dict[str, Any]:
		entity = self.entities[entity_id]
		return {
			"id": entity["id"],
			"name": entity["name"],
			"type": entity["type"],
			"aliases": sorted(entity["aliases"]),
			"mention_count": entity["mention_count"],
			"metadata": dict(entity["metadata"]),
			"description": entity["metadata"].get("description", ""),
		}

	def _relation_dict(self, relation_id: str) -> dict[str, Any]:
		relation = self.relations[relation_id]
		source = self.entities.get(relation["source_id"])
		target = self.entities.get(relation["target_id"])
		return {
			"id": relation["id"],
			"source_id": relation["source_id"],
			"target_id": relation["target_id"],
			"source_name": source["name"] if source else "",
			"target_name": target["name"] if target else "",
			"relation_type": relation["relation_type"],
			"description": relation["description"],
			"confidence": relation["confidence"],
			"source_memory_id": relation["source_memory_id"],
			"metadata": dict(relation["metadata"]),
		}

	def _resolve_entity(self, name: str, entity_type: str, aliases: list[str]) -> dict[str, Any] | None:
		name_key = _normalize_entity_text(name)
		type_key = _normalize_entity_text(entity_type or "concept")
		alias_keys = {_normalize_entity_text(alias) for alias in aliases if alias}
		for entity in self.entities.values():
			entity_type_key = _normalize_entity_text(entity["type"] or "concept")
			if type_key and type_key != "concept" and entity_type_key and type_key != entity_type_key:
				continue
			names = {_normalize_entity_text(entity["name"])}
			names.update(_normalize_entity_text(alias) for alias in entity["aliases"])
			if name_key in names or names & alias_keys:
				return entity
		return None


def _normalize_entity_text(value: str) -> str:
	return " ".join(str(value).strip().lower().split())
