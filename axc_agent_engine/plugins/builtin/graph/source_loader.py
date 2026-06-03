"""Graph source ingestion.
中文：此文档说明相关引擎组件的行为。"""
import logging
import os
from collections.abc import Callable
from typing import Any

from .config import GraphConfig
from .policy import GraphPolicy
from .utils import clean_text

logger = logging.getLogger(__name__)


class GraphSourceLoader:
	def __init__(
		self,
		config: GraphConfig,
		store: Any,
		policy: GraphPolicy,
		load_errors: list[dict[str, Any]],
		read_source: Callable[[str], tuple[list[dict], list[dict]]],
		entity_count: Callable[[], int],
		relation_count: Callable[[], int],
	) -> None:
		self._config = config
		self._store = store
		self._policy = policy
		self._load_errors = load_errors
		self._read_source = read_source
		self._entity_count = entity_count
		self._relation_count = relation_count

	def load(self) -> dict[str, Any]:
		self._load_errors.clear()
		loaded_entities = 0
		loaded_relations = 0
		for source in self._config.sources:
			real_source = os.path.realpath(source)
			if not os.path.exists(real_source):
				self._fail({"source": source, "error": "source not found"})
			entities, relations = self._read_source(real_source)
			external_names: dict[str, str] = {}
			for item in entities:
				if self._entity_count() >= self._config.max_entities:
					self._fail({"source": source, "error": "entity limit reached"})
				entity_type = str(item.get("type") or item.get("entity_type") or "concept")
				type_error = self._policy.validate_entity_type(entity_type)
				if type_error:
					self._fail({"source": source, "entity": item.get("id", item.get("name", "")), "error": type_error})
				entity = self._store.upsert_entity(
					clean_text(item.get("name", ""), self._config.max_name_length),
					entity_type,
					[clean_text(alias, self._config.max_name_length) for alias in item.get("aliases", []) if str(alias).strip()],
					description=clean_text(item.get("description", ""), self._config.max_description_length),
					external_id=str(item.get("id", "")),
					namespace=self._config.namespace,
					source=source,
				)
				if item.get("id"):
					external_names[str(item["id"])] = entity["name"]
				loaded_entities += 1
			for item in relations:
				if self._relation_count() >= self._config.max_relations:
					self._fail({"source": source, "error": "relation limit reached"})
				relation_type = str(item.get("relation_type", "RELATED_TO"))
				type_error = self._policy.validate_relation_type(relation_type)
				if type_error:
					self._fail({"source": source, "relation": item.get("id", ""), "error": type_error})
				source_name = str(item.get("source") or item.get("source_name") or external_names.get(str(item.get("source_id", "")), item.get("source_id", "")))
				target_name = str(item.get("target") or item.get("target_name") or external_names.get(str(item.get("target_id", "")), item.get("target_id", "")))
				if not source_name or not target_name:
					self._fail({"source": source, "relation": item.get("id", ""), "error": "source/target missing"})
					self._store.upsert_relation(
						source_name,
						target_name,
						relation_type,
						clean_text(item.get("description", ""), self._config.max_description_length),
						source_memory_id=str(item.get("source_memory_id", "")),
						external_id=str(item.get("id", "")),
						namespace=self._config.namespace,
						data_source=source,
					)
				loaded_relations += 1
		source_stats = {"entities": loaded_entities, "relations": loaded_relations, "sources": len(self._config.sources)}
		logger.info("[graph] Loaded %s entities, %s relations", loaded_entities, loaded_relations)
		return source_stats

	def _fail(self, error: dict[str, Any]) -> None:
		self._load_errors.append(error)
		raise ValueError(str(error))
