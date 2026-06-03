"""Tests for plugin config_schema declarations.
中文：插件 config_schema 声明测试。"""
from __future__ import annotations

import pytest

from axc_agent_engine.core.errors import PluginInitError
from axc_agent_engine.engine import AgentModels, Engine
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin import AVAILABLE_BUILTIN_PLUGINS
from axc_agent_engine.plugins.builtin.compress.plugin import CompressPlugin
from axc_agent_engine.plugins.config_schema import (
	array_field,
	config_field,
	config_schema,
	object_field,
	validate_plugin_config,
)
from axc_agent_engine.plugins.registry import PluginRegistry


class SchemaOnlyPlugin(BasePlugin):
	name = "schema_only"
	display_name = "Schema 测试"
	config_schema = config_schema("schema_only", "Schema 测试", "用于验证未知配置兼容。", [])

	def initialize(self, config: dict, plugin_ctx) -> None:
		super().initialize(config, plugin_ctx)
		self.config = config


def test_all_available_builtin_plugins_declare_config_schema():
	registry = PluginRegistry()
	registry.register_many(AVAILABLE_BUILTIN_PLUGINS.values())
	assert set(registry.list_plugin_config_schemas()) == set(AVAILABLE_BUILTIN_PLUGINS)


def test_schema_query_contains_common_fields():
	registry = PluginRegistry()
	registry.register(CompressPlugin)
	schema = registry.get_plugin_config_schema("compress")
	fields = {field.key: field for field in schema.fields}
	assert fields["enabled"].label == "启用"
	assert fields["enabled"].default is True
	assert "required" not in fields


def test_compress_schema_has_nested_summary_and_windows():
	registry = PluginRegistry()
	registry.register(CompressPlugin)
	schema = registry.get_plugin_config_schema("compress")
	fields = {field.key: field for field in schema.fields}
	summary = {field.key: field for field in fields["summary"].children}
	context_window = {field.key: field for field in fields["context_window"].children}
	recent_window = {field.key: field for field in fields["recent_window"].children}
	assert summary["after_rounds"].default == 8
	assert summary["max_tokens"].default == 800
	assert context_window["max_input_tokens"].default == 24000
	assert recent_window["rounds"].default == 4


def test_mcp_schema_has_servers_array_object_fields():
	registry = PluginRegistry()
	registry.register(AVAILABLE_BUILTIN_PLUGINS["mcp"])
	schema = registry.get_plugin_config_schema("mcp")
	fields = {field.key: field for field in schema.fields}
	servers = fields["servers"]
	assert servers.type == "array"
	assert servers.item_schema is not None
	server_fields = {field.key: field for field in servers.item_schema.children}
	assert server_fields["command"].type == "string"
	assert "required" not in server_fields
	assert server_fields["url"].default is None


def test_agent_template_instantiate_rejects_extra_unknown_plugin_key(mock_llm, tmp_path):
	registry = PluginRegistry()
	registry.register(SchemaOnlyPlugin)
	path = tmp_path / "agent.yaml"
	path.write_text(
		"""
name: schema_agent
plugins:
  schema_only:
    enabled: true
    unknown_key: still_allowed
""",
		encoding="utf-8",
	)
	engine = Engine(plugin_registry=registry)
	with pytest.raises(PluginInitError, match="Unknown config field"):
		engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=mock_llm))


def test_validate_plugin_config_rejects_boundary_errors():
	schema = config_schema("strict", "Strict", "Strict schema.", [
		config_field("mode", "Mode", "string", "Mode.", enum=["a", "b"], required=True),
		config_field("count", "Count", "integer", "Count."),
		config_field("ratio", "Ratio", "number", "Ratio."),
		config_field("flag", "Flag", "boolean", "Flag."),
		object_field("nested", "Nested", "Nested object.", [
			config_field("name", "Name", "string", "Name."),
		]),
		array_field("items", "Items", "String items.", config_field("", "Item", "string", "Item.")),
		config_field("free", "Free", "object", "Free object."),
	])
	validate_plugin_config("strict", {
		"mode": "a",
		"count": 1,
		"ratio": 1.5,
		"flag": False,
		"nested": {"name": "ok"},
		"items": ["x"],
		"free": {"any": {"shape": True}},
	}, schema)
	cases = [
		({"mode": "a", "unknown": 1}, "Unknown config field"),
		({"count": 1}, "Missing required"),
		({"mode": "z"}, "must be one of"),
		({"mode": "a", "count": True}, "integer"),
		({"mode": "a", "ratio": True}, "number"),
		({"mode": "a", "flag": "yes"}, "boolean"),
		({"mode": "a", "nested": []}, "object"),
		({"mode": "a", "nested": {"bad": "x"}}, "Unknown config field"),
		({"mode": "a", "items": "x"}, "array"),
		({"mode": "a", "items": [1]}, "string"),
	]
	for config, pattern in cases:
		with pytest.raises(ValueError, match=pattern):
			validate_plugin_config("strict", config, schema)


def test_validate_plugin_config_required_none_is_invalid():
	schema = config_schema("strict", "Strict", "Strict schema.", [
		config_field("name", "Name", "string", "Name.", required=True),
	])
	with pytest.raises(ValueError, match="required"):
		validate_plugin_config("strict", {"name": None}, schema)
