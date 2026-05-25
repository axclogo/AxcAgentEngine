"""Graph plugin configuration."""
from dataclasses import dataclass
from typing import Any

from axc_agent_engine.plugins.builtin.common import bounded_int


@dataclass(frozen=True)
class GraphConfig:
	enabled: bool = True
	sources: tuple[str, ...] = ()
	namespace: str = "default"
	allow_writes: bool = True
	allow_deletes: bool = True
	allowed_entity_types: frozenset[str] = frozenset()
	denied_entity_types: frozenset[str] = frozenset()
	allowed_relation_types: frozenset[str] = frozenset()
	denied_relation_types: frozenset[str] = frozenset()
	max_entities: int = 100_000
	max_relations: int = 500_000
	max_depth: int = 3
	default_limit: int = 20
	max_limit: int = 100
	max_name_length: int = 256
	max_description_length: int = 4000
	max_result_bytes: int = 256_000
	include_metadata: bool = True
	audit_enabled: bool = True

	@classmethod
	def from_dict(cls, config: dict[str, Any]) -> "GraphConfig":
		return cls(
			enabled=bool(config.get("enabled", True)),
			sources=tuple(str(source) for source in config.get("sources", [])),
			namespace=str(config.get("namespace", "default")),
			allow_writes=bool(config.get("allow_writes", True)),
			allow_deletes=bool(config.get("allow_deletes", True)),
			allowed_entity_types=frozenset(str(t) for t in config.get("allowed_entity_types", [])),
			denied_entity_types=frozenset(str(t) for t in config.get("denied_entity_types", [])),
			allowed_relation_types=frozenset(str(t) for t in config.get("allowed_relation_types", [])),
			denied_relation_types=frozenset(str(t) for t in config.get("denied_relation_types", [])),
			max_entities=bounded_int(config.get("max_entities", 100_000), 1, 10_000_000),
			max_relations=bounded_int(config.get("max_relations", 500_000), 1, 50_000_000),
			max_depth=bounded_int(config.get("max_depth", 3), 0, 20),
			default_limit=bounded_int(config.get("default_limit", 20), 1, 1000),
			max_limit=bounded_int(config.get("max_limit", 100), 1, 10_000),
			max_name_length=bounded_int(config.get("max_name_length", 256), 1, 4096),
			max_description_length=bounded_int(config.get("max_description_length", 4000), 0, 200_000),
			max_result_bytes=bounded_int(config.get("max_result_bytes", 256_000), 1, 50 * 1024 * 1024),
			include_metadata=bool(config.get("include_metadata", True)),
			audit_enabled=bool(config.get("audit", True)),
		)
