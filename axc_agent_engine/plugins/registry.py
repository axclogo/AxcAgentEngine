"""Plugin registry.
插件注册表。"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from axc_agent_engine.core.errors import PluginInitError
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.config_schema import PluginConfigSchema, normalize_plugin_config_schema

PluginFactory = Callable[[], BasePlugin]


class PluginRegistry:
	"""Engine 级插件注册表。
	Engine-scoped plugin registry.
	"""

	def __init__(self) -> None:
		self._factories: dict[str, PluginFactory] = {}

	def register(self, plugin_cls: type[BasePlugin]) -> None:
		"""Register a plugin class.
		注册插件类。"""
		name = self._class_name(plugin_cls)
		if not issubclass(plugin_cls, BasePlugin):
			raise PluginInitError(f"Plugin {plugin_cls.__name__} must inherit BasePlugin")
		self.register_factory(name, plugin_cls)

	def register_many(self, plugin_classes: Iterable[type[BasePlugin]]) -> None:
		"""Register multiple plugin classes.
		批量注册插件类。"""
		for plugin_cls in plugin_classes:
			self.register(plugin_cls)

	def register_factory(self, name: str, factory: PluginFactory) -> None:
		"""Register a plugin factory.
		注册插件工厂。"""
		if not name:
			raise PluginInitError("Plugin name cannot be empty")
		if name in self._factories:
			raise PluginInitError(f"Duplicate plugin registered: {name}")
		self._validate_factory_schema(name, factory)
		self._factories[name] = factory

	def create(self, name: str) -> BasePlugin | None:
		"""Create a registered plugin instance.
		创建已注册插件实例。"""
		factory = self._factories.get(name)
		if not factory:
			return None
		plugin = factory()
		self._validate_instance(name, plugin)
		return plugin

	def get(self, name: str) -> PluginFactory | None:
		"""Return a plugin factory.
		返回插件工厂。"""
		return self._factories.get(name)

	def names(self) -> list[str]:
		"""Return registered plugin names.
		返回已注册插件名。"""
		return sorted(self._factories)

	def get_plugin_config_schema(self, plugin_name: str) -> PluginConfigSchema:
		"""Return one plugin configuration schema.
		返回单个插件的配置 Schema。"""
		factory = self._factories.get(plugin_name)
		if not factory:
			raise PluginInitError(f"Plugin {plugin_name} is not registered")
		return self._schema_from_factory(plugin_name, factory)

	def list_plugin_config_schemas(self) -> dict[str, PluginConfigSchema]:
		"""Return configuration schemas for all registered plugins.
		返回所有已注册插件的配置 Schema。"""
		return {name: self._schema_from_factory(name, self._factories[name]) for name in self.names()}

	def __contains__(self, name: str) -> bool:
		return name in self._factories

	@staticmethod
	def _class_name(plugin_cls: type[BasePlugin]) -> str:
		name = str(getattr(plugin_cls, "name", "") or "")
		if not name:
			raise PluginInitError(f"Plugin {plugin_cls.__name__} must declare a non-empty name")
		return name

	@staticmethod
	def _validate_instance(config_name: str, plugin: BasePlugin) -> None:
		if not isinstance(plugin, BasePlugin):
			raise PluginInitError(f"Registered plugin {config_name} must inherit BasePlugin")
		if not plugin.name:
			raise PluginInitError(f"Registered plugin {config_name} must declare a non-empty name")
		if plugin.name != config_name:
			raise PluginInitError(f"Plugin name mismatch: config key {config_name}, plugin declares {plugin.name}")
		if plugin.config_schema is None:
			raise PluginInitError(f"Registered plugin {config_name} must declare config_schema")

	@classmethod
	def _validate_factory_schema(cls, name: str, factory: PluginFactory) -> None:
		try:
			cls._schema_from_factory(name, factory)
		except PluginInitError:
			raise
		except Exception as e:
			raise PluginInitError(f"Plugin {name} config_schema is invalid: {e}") from e

	@classmethod
	def _schema_from_factory(cls, name: str, factory: PluginFactory) -> PluginConfigSchema:
		plugin = factory()
		cls._validate_instance(name, plugin)
		try:
			return normalize_plugin_config_schema(
				plugin.config_schema,
				plugin_name=name,
				display_name=plugin.display_name,
			)
		except Exception as e:
			raise PluginInitError(f"Plugin {name} config_schema is invalid: {e}") from e
