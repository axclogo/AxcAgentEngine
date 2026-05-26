"""In-memory entity relationship graph for memory retrieval.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class GraphEntity:
	id: str
	name: str
	entity_type: str = "concept"
	aliases: set[str] = field(default_factory=set)
	mention_count: int = 1
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphRelation:
	id: str
	source_id: str
	target_id: str
	relation_type: str = "RELATED_TO"
	description: str = ""
	confidence: float = 0.8
	source_memory_id: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class EntityResolver(Protocol):
	"""Resolves whether a mention should merge with an existing entity.
中文：此文档说明相关引擎组件的行为。"""
	def resolve(
		self,
		name: str,
		entity_type: str,
		aliases: list[str],
		entities: dict[str, GraphEntity],
	) -> GraphEntity | None: ...


class DefaultEntityResolver:
	"""Type-aware alias resolver for graph entity disambiguation.
中文：此文档说明相关引擎组件的行为。"""

	def resolve(
		self,
		name: str,
		entity_type: str,
		aliases: list[str],
		entities: dict[str, GraphEntity],
	) -> GraphEntity | None:
		name_key = _normalize_entity_text(name)
		type_key = _normalize_entity_type(entity_type)
		alias_keys = {_normalize_entity_text(alias) for alias in aliases if alias}
		for entity in entities.values():
			entity_type_key = _normalize_entity_type(entity.entity_type)
			if type_key and type_key != "concept" and entity_type_key and type_key != entity_type_key:
				continue
			names = {_normalize_entity_text(entity.name)}
			names.update(_normalize_entity_text(alias) for alias in entity.aliases)
			if name_key in names or names & alias_keys:
				return entity
		return None


class GraphMemory:
	"""Entity disambiguation and relation storage.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, resolver: EntityResolver | None = None) -> None:
		self.entities: dict[str, GraphEntity] = {}
		self.relations: dict[str, GraphRelation] = {}
		self.resolver = resolver or DefaultEntityResolver()

	def upsert_entity(self, name: str, entity_type: str = "concept",
					  aliases: list[str] | None = None) -> GraphEntity:
		name = name.strip()
		if not name:
			raise ValueError("entity name cannot be empty")
		aliases = aliases or []
		existing = self.resolve_entity(name, entity_type=entity_type, aliases=aliases)
		if existing:
			existing.mention_count += 1
			existing.aliases.update(aliases)
			if entity_type and existing.entity_type == "concept":
				existing.entity_type = entity_type
			return existing
		entity_id = f"entity:{len(self.entities) + 1}"
		entity = GraphEntity(id=entity_id, name=name, entity_type=entity_type, aliases=set(aliases))
		self.entities[entity_id] = entity
		return entity

	def resolve_entity(self, name: str, entity_type: str = "", aliases: list[str] | None = None) -> GraphEntity | None:
		return self.resolver.resolve(name, entity_type, aliases or [], self.entities)

	def upsert_relation(self, source_name: str, target_name: str, relation_type: str = "RELATED_TO",
						description: str = "", source_memory_id: str = "") -> GraphRelation:
		source = self.upsert_entity(source_name, "concept")
		target = self.upsert_entity(target_name, "concept")
		for relation in self.relations.values():
			if (relation.source_id == source.id and relation.target_id == target.id
					and relation.relation_type == relation_type):
				if len(description) > len(relation.description):
					relation.description = description
				relation.confidence = min(relation.confidence + 0.05, 1.0)
				return relation
		relation_id = f"relation:{len(self.relations) + 1}"
		relation = GraphRelation(
			id=relation_id,
			source_id=source.id,
			target_id=target.id,
			relation_type=relation_type,
			description=description,
			source_memory_id=source_memory_id,
		)
		self.relations[relation_id] = relation
		return relation

	def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
		query_lower = query.lower()
		matches: list[dict[str, Any]] = []
		for relation in self.relations.values():
			source = self.entities.get(relation.source_id)
			target = self.entities.get(relation.target_id)
			if not source or not target:
				continue
			haystack = f"{source.name} {target.name} {relation.relation_type} {relation.description}".lower()
			if query_lower and query_lower not in haystack:
				words = [w for w in query_lower.split() if w]
				if not any(w in haystack for w in words):
					continue
			matches.append({
				"source": source.name,
				"target": target.name,
				"relation_type": relation.relation_type,
				"description": relation.description,
				"source_memory_id": relation.source_memory_id,
				"confidence": relation.confidence,
			})
		return matches[:limit]


def _normalize_entity_text(value: str) -> str:
	return " ".join(str(value).strip().lower().split())


def _normalize_entity_type(value: str) -> str:
	return _normalize_entity_text(value or "concept")
