"""Tests for plugin config_schema declarations.
中文：插件 config_schema 声明测试。"""
from __future__ import annotations

from axc_agent_engine.engine import Engine
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin import AVAILABLE_BUILTIN_PLUGINS
from axc_agent_engine.plugins.builtin.compress.plugin import CompressPlugin
from axc_agent_engine.plugins.config_schema import config_schema
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
	assert fields["required"].label == "必须加载"
	assert fields["required"].default is False


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
	assert server_fields["required"].default is False
	assert server_fields["url"].default is None


def test_old_yaml_load_agent_keeps_extra_unknown_plugin_key(mock_llm, tmp_path):
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
	engine = Engine(default_llm=mock_llm, plugin_registry=registry)
	agent = engine.load_agent(str(path))
	assert agent._plugins[0].config["unknown_key"] == "still_allowed"
