"""插件注册表。
Plugin registry.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from axc_agent_engine.core.errors import PluginInitError
from axc_agent_engine.plugins.base import BasePlugin


PluginFactory = Callable[[], BasePlugin]


class PluginRegistry:
	"""Engine 级插件注册表。
	Engine-scoped plugin registry.
	"""

	def __init__(self) -> None:
		self._factories: dict[str, PluginFactory] = {}

	def register(self, plugin_cls: type[BasePlugin]) -> None:
		"""注册插件类。
		Register a plugin class.
		"""
		name = self._class_name(plugin_cls)
		if not issubclass(plugin_cls, BasePlugin):
			raise PluginInitError(f"Plugin {plugin_cls.__name__} must inherit BasePlugin")
		self.register_factory(name, plugin_cls)

	def register_many(self, plugin_classes: Iterable[type[BasePlugin]]) -> None:
		"""批量注册插件类。
		Register multiple plugin classes.
		"""
		for plugin_cls in plugin_classes:
			self.register(plugin_cls)

	def register_factory(self, name: str, factory: PluginFactory) -> None:
		"""注册插件工厂。
		Register a plugin factory.
		"""
		if not name:
			raise PluginInitError("Plugin name cannot be empty")
		if name in self._factories:
			raise PluginInitError(f"Duplicate plugin registered: {name}")
		self._factories[name] = factory

	def create(self, name: str) -> BasePlugin | None:
		"""创建已注册插件实例。
		Create a registered plugin instance.
		"""
		factory = self._factories.get(name)
		if not factory:
			return None
		plugin = factory()
		self._validate_instance(name, plugin)
		return plugin

	def get(self, name: str) -> PluginFactory | None:
		"""返回插件工厂。
		Return a plugin factory.
		"""
		return self._factories.get(name)

	def names(self) -> list[str]:
		"""返回已注册插件名。
		Return registered plugin names.
		"""
		return sorted(self._factories)

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
