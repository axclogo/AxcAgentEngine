"""Graph entity/relation type policy."""


class GraphPolicy:
	def __init__(
		self,
		allowed_entity_types: set[str],
		denied_entity_types: set[str],
		allowed_relation_types: set[str],
		denied_relation_types: set[str],
	) -> None:
		self.allowed_entity_types = allowed_entity_types
		self.denied_entity_types = denied_entity_types
		self.allowed_relation_types = allowed_relation_types
		self.denied_relation_types = denied_relation_types

	def validate_entity_type(self, entity_type: str) -> str:
		if entity_type in self.denied_entity_types:
			return f"entity_type '{entity_type}' is denied"
		if self.allowed_entity_types and entity_type not in self.allowed_entity_types:
			return f"entity_type '{entity_type}' is not allowed"
		return ""

	def validate_relation_type(self, relation_type: str) -> str:
		if relation_type in self.denied_relation_types:
			return f"relation_type '{relation_type}' is denied"
		if self.allowed_relation_types and relation_type not in self.allowed_relation_types:
			return f"relation_type '{relation_type}' is not allowed"
		return ""
