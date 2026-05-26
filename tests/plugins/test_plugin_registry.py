"""Tests for explicit PluginRegistry behavior."""
from __future__ import annotations

import pytest

from axc_agent_engine.core.errors import PluginInitError
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin import AVAILABLE_BUILTIN_PLUGINS
from axc_agent_engine.plugins.builtin.safety.plugin import SafetyPlugin
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
	assert load_plugins({"safety": {"enabled": True}}, PluginContext(), registry) == []
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


def test_registered_factory_must_return_base_plugin():
	registry = PluginRegistry()
	registry.register_factory("bad", lambda: object())
	with pytest.raises(PluginInitError, match="must inherit BasePlugin"):
		registry.create("bad")


def test_registered_factory_must_return_matching_plugin_name():
	class CustomPlugin(BasePlugin):
		name = "actual"

	registry = PluginRegistry()
	registry.register_factory("configured", CustomPlugin)
	with pytest.raises(PluginInitError, match="Plugin name mismatch"):
		load_plugins({"configured": {"enabled": True}}, PluginContext(), registry)
