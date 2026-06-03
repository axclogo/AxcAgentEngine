"""Plugin configuration schema models.
中文：插件配置 Schema 模型。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PluginConfigFieldType = Literal["string", "integer", "number", "boolean", "object", "array"]


class PluginConfigField(BaseModel):
	"""One plugin YAML field descriptor.
中文：单个插件 YAML 字段描述。"""
	key: str
	label: str = ""
	label_en: str = ""
	type: PluginConfigFieldType = "string"
	description: str = ""
	default: Any = None
	required: bool = False
	enum: list[Any] = Field(default_factory=list)
	children: list["PluginConfigField"] = Field(default_factory=list)
	item_schema: "PluginConfigField | None" = None
	advanced: bool = False
	deprecated: bool = False


class PluginConfigSchema(BaseModel):
	"""Structured schema declared by a plugin.
中文：插件声明的结构化配置 Schema。"""
	plugin_name: str = ""
	display_name: str = ""
	display_name_en: str = ""
	description: str = ""
	fields: list[PluginConfigField] = Field(default_factory=list)


def config_field(
	key: str,
	label: str,
	field_type: PluginConfigFieldType,
	description: str,
	*,
	label_en: str = "",
	default: Any = None,
	required: bool = False,
	enum: list[Any] | None = None,
	children: list[PluginConfigField] | None = None,
	item_schema: PluginConfigField | None = None,
	advanced: bool = False,
	deprecated: bool = False,
) -> PluginConfigField:
	"""Build one field descriptor with compact call sites.
中文：用紧凑调用方式构建字段描述。"""
	return PluginConfigField(
		key=key,
		label=label,
		label_en=label_en,
		type=field_type,
		description=description,
		default=default,
		required=required,
		enum=list(enum or []),
		children=list(children or []),
		item_schema=item_schema,
		advanced=advanced,
		deprecated=deprecated,
	)


def object_field(
	key: str,
	label: str,
	description: str,
	children: list[PluginConfigField],
	*,
	label_en: str = "",
	default: Any = None,
	required: bool = False,
	advanced: bool = False,
	deprecated: bool = False,
) -> PluginConfigField:
	"""Build a nested object field.
中文：构建嵌套对象字段。"""
	return config_field(
		key,
		label,
		"object",
		description,
		label_en=label_en,
		default={} if default is None else default,
		required=required,
		children=children,
		advanced=advanced,
		deprecated=deprecated,
	)


def array_field(
	key: str,
	label: str,
	description: str,
	item_schema: PluginConfigField,
	*,
	label_en: str = "",
	default: Any = None,
	required: bool = False,
	advanced: bool = False,
	deprecated: bool = False,
) -> PluginConfigField:
	"""Build an array field with item schema.
中文：构建带元素 Schema 的数组字段。"""
	return config_field(
		key,
		label,
		"array",
		description,
		label_en=label_en,
		default=[] if default is None else default,
		required=required,
		item_schema=item_schema,
		advanced=advanced,
		deprecated=deprecated,
	)


def config_schema(
	plugin_name: str,
	display_name: str,
	description: str,
	fields: list[PluginConfigField],
	*,
	display_name_en: str = "",
) -> PluginConfigSchema:
	"""Build a plugin-level schema descriptor.
中文：构建插件级配置 Schema 描述。"""
	return PluginConfigSchema(
		plugin_name=plugin_name,
		display_name=display_name,
		display_name_en=display_name_en,
		description=description,
		fields=fields,
	)


def normalize_plugin_config_schema(
	raw_schema: PluginConfigSchema | dict | None,
	*,
	plugin_name: str,
	display_name: str = "",
) -> PluginConfigSchema:
	"""Normalize a declared schema and add shared fields.
中文：规范化插件声明 Schema 并补充通用字段。"""
	if raw_schema is None:
		raise ValueError(f"Plugin {plugin_name} must declare config_schema")
	schema = raw_schema if isinstance(raw_schema, PluginConfigSchema) else PluginConfigSchema(**raw_schema)
	if not schema.plugin_name:
		schema.plugin_name = plugin_name
	if schema.plugin_name != plugin_name:
		raise ValueError(f"Plugin schema name mismatch: {schema.plugin_name} != {plugin_name}")
	if not schema.display_name:
		schema.display_name = display_name or plugin_name
	schema.fields = _with_common_fields(schema.fields)
	return schema


def validate_plugin_config(plugin_name: str, config: dict[str, Any], schema: PluginConfigSchema) -> None:
	"""Strictly validate plugin YAML against declared schema.
中文：按插件声明 Schema 严格校验 YAML 配置。"""
	if not isinstance(config, dict):
		raise ValueError(f"Plugin {plugin_name} config must be an object")
	_validate_object(config, schema.fields, f"plugins.{plugin_name}")


def _with_common_fields(fields: list[PluginConfigField]) -> list[PluginConfigField]:
	"""Add shared plugin fields unless already declared.
中文：补充插件通用字段，已有声明则保留。"""
	by_key = {field.key for field in fields}
	common = []
	if "enabled" not in by_key:
		common.append(config_field(
			"enabled",
			"启用",
			"boolean",
			"是否启用插件",
			label_en="Enabled",
			default=True,
		))
	return common + list(fields)


def _validate_object(value: dict[str, Any], fields: list[PluginConfigField], path: str) -> None:
	field_map = {field.key: field for field in fields if field.key}
	for key in value:
		if key not in field_map:
			raise ValueError(f"Unknown config field: {path}.{key}")
	for field in fields:
		if not field.key:
			continue
		child_path = f"{path}.{field.key}"
		if field.required and field.key not in value:
			raise ValueError(f"Missing required config field: {child_path}")
		if field.key in value:
			_validate_value(value[field.key], field, child_path)


def _validate_value(value: Any, field: PluginConfigField, path: str) -> None:
	if value is None:
		if field.required:
			raise ValueError(f"Config field {path} is required")
		return
	if field.enum and value not in field.enum:
		raise ValueError(f"Config field {path} must be one of {field.enum}, got {value!r}")
	if field.type == "string":
		_require_type(value, str, path, "string")
	elif field.type == "integer":
		if isinstance(value, bool) or not isinstance(value, int):
			raise ValueError(f"Config field {path} must be an integer")
	elif field.type == "number":
		if isinstance(value, bool) or not isinstance(value, (int, float)):
			raise ValueError(f"Config field {path} must be a number")
	elif field.type == "boolean":
		_require_type(value, bool, path, "boolean")
	elif field.type == "object":
		_require_type(value, dict, path, "object")
		if field.children:
			_validate_object(value, field.children, path)
	elif field.type == "array":
		if not isinstance(value, list):
			raise ValueError(f"Config field {path} must be an array")
		if field.item_schema:
			for idx, item in enumerate(value):
				_validate_value(item, field.item_schema, f"{path}[{idx}]")


def _require_type(value: Any, expected: type, path: str, label: str) -> None:
	if not isinstance(value, expected):
		raise ValueError(f"Config field {path} must be a {label}")
