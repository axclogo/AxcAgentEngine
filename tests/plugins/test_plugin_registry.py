"""Tests for explicit PluginRegistry behavior."""
from __future__ import annotations

import pytest

from axc_agent_engine.core.errors import PluginInitError
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin import AVAILABLE_BUILTIN_PLUGINS
from axc_agent_engine.plugins.builtin.safety.plugin import SafetyPlugin
from axc_agent_engine.plugins.config_schema import config_schema
from axc_agent_engine.plugins.loader import load_plugins
from axc_agent_engine.plugins.registry import PluginRegistry


def test_registry_starts_empty():
	registry = PluginRegistry()
	assert registry.names() == []


def test_available_builtin_plugins_are_not_registered_automatically():
	registry = PluginRegistry()
	assert "safety" in AVAILABLE_BUILTIN_PLUGINS
	assert "safety" not in registry


def test_load_plugins_uses_only_explicit_registry():
	registry = PluginRegistry()
	with pytest.raises(PluginInitError, match="configured but not registered"):
		load_plugins({"safety": {"enabled": True}}, PluginContext(), registry)
	registry.register(SafetyPlugin)
	plugins = load_plugins({"safety": {"enabled": True}}, PluginContext(), registry)
	assert [plugin.name for plugin in plugins] == ["safety"]


def test_register_many():
	registry = PluginRegistry()
	registry.register_many([SafetyPlugin])
	assert registry.names() == ["safety"]


def test_register_requires_base_plugin_subclass():
	class StructuralPlugin:
		name = "structural"

		def initialize(self, config, plugin_ctx):
			pass

		def get_tools(self):
			return []

	registry = PluginRegistry()
	with pytest.raises(PluginInitError, match="must inherit BasePlugin"):
		registry.register(StructuralPlugin)


def test_register_requires_config_schema():
	class MissingSchemaPlugin(BasePlugin):
		name = "missing_schema"

	registry = PluginRegistry()
	with pytest.raises(PluginInitError, match="must declare config_schema"):
		registry.register(MissingSchemaPlugin)


def test_registered_factory_must_return_base_plugin():
	registry = PluginRegistry()
	with pytest.raises(PluginInitError, match="must inherit BasePlugin"):
		registry.register_factory("bad", lambda: object())


def test_registered_factory_must_return_matching_plugin_name():
	class CustomPlugin(BasePlugin):
		name = "actual"
		config_schema = config_schema("actual", "测试插件", "测试用插件配置。", [])

	registry = PluginRegistry()
	with pytest.raises(PluginInitError, match="Plugin name mismatch"):
		registry.register_factory("configured", CustomPlugin)


def test_list_plugin_config_schemas_adds_common_fields():
	registry = PluginRegistry()
	registry.register(SafetyPlugin)
	schemas = registry.list_plugin_config_schemas()
	field_names = [field.key for field in schemas["safety"].fields]
	assert field_names[0] == "enabled"
	assert "required" not in field_names
	assert schemas["safety"].fields[0].default is True


def test_get_plugin_config_schema_returns_builtin_schema():
	registry = PluginRegistry()
	registry.register(SafetyPlugin)
	schema = registry.get_plugin_config_schema("safety")
	assert schema.plugin_name == "safety"
	assert any(field.key == "prompt_injection" for field in schema.fields)
