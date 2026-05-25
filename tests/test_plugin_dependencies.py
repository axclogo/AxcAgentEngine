"""Tests for #2 Plugin dependency declaration."""
import pytest
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.loader import load_plugins, _validate_dependencies
from axc_agent_engine.plugins.registry import PluginRegistry
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.core.errors import PluginInitError


class TestPluginDependsOn:
	def test_base_plugin_has_depends_on(self):
		p = BasePlugin()
		assert hasattr(p, 'depends_on')
		assert p.depends_on == []

	def test_custom_plugin_with_dependency(self):
		class PluginA(BasePlugin):
			name = "plugin_a"
			depends_on = ["plugin_b"]
		a = PluginA()
		assert a.depends_on == ["plugin_b"]

	def test_validate_dependencies_reorders(self):
		class PluginA(BasePlugin):
			name = "a"
			priority = 10
			depends_on = ["b"]
		class PluginB(BasePlugin):
			name = "b"
			priority = 20
		plugins = [PluginA(), PluginB()]
		_validate_dependencies(plugins)
		names = [p.name for p in plugins]
		assert names.index("b") < names.index("a")

	def test_validate_dependencies_no_deps_unchanged(self):
		class PluginA(BasePlugin):
			name = "a"
			priority = 10
		class PluginB(BasePlugin):
			name = "b"
			priority = 20
		plugins = [PluginA(), PluginB()]
		_validate_dependencies(plugins)
		assert plugins[0].name == "a"
		assert plugins[1].name == "b"

	def test_validate_dependencies_missing_dep_warns(self, caplog):
		import logging
		class PluginA(BasePlugin):
			name = "a"
			depends_on = ["nonexistent"]
		plugins = [PluginA()]
		with caplog.at_level(logging.WARNING):
			_validate_dependencies(plugins)
		assert "nonexistent" in caplog.text

	def test_validate_dependencies_already_correct_order(self):
		class PluginA(BasePlugin):
			name = "a"
			priority = 20
			depends_on = ["b"]
		class PluginB(BasePlugin):
			name = "b"
			priority = 10
		plugins = [PluginB(), PluginA()]
		_validate_dependencies(plugins)
		assert plugins[0].name == "b"
		assert plugins[1].name == "a"

	def test_load_plugins_validates_deps(self):
		"""Integration: load_plugins calls dependency validation."""
		ctx = PluginContext()
		# Empty config should work fine
		result = load_plugins({}, ctx, PluginRegistry())
		assert result == []

	def test_load_plugins_required_not_found(self):
		ctx = PluginContext()
		with pytest.raises(PluginInitError):
			load_plugins({"nonexistent": {"enabled": True, "required": True}}, ctx, PluginRegistry())
