"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
插件加载器。
Plugin loader."""
import logging

from axc_agent_engine.core.errors import PluginInitError
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


def load_plugins(
	plugins_config: dict[str, dict],
	ctx: PluginContext,
	registry: PluginRegistry,
) -> list[BasePlugin]:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
加载并初始化所有启用插件，按 phase+priority 排序并校验依赖。
	Load and initialize enabled plugins, then sort and validate dependencies.
	"""
	active: list[BasePlugin] = []
	for name, config in plugins_config.items():
		if not config.get("enabled", False):
			continue
		plugin = registry.create(name)
		if plugin is None:
			if config.get("required", False):
				raise PluginInitError(f"Required plugin {name} is enabled but not registered")
			logger.warning(f"Plugin {name} is enabled but not registered, skipping")
			continue
		_validate_plugin_name(name, plugin)
		try:
			plugin.initialize(config, ctx)
			active.append(plugin)
			logger.info(f"Plugin {name} loaded (priority={plugin.priority})")
		except Exception as e:
			if config.get("required", False):
				raise PluginInitError(f"Required plugin {name} initialization failed: {e}") from e
			logger.error(f"Plugin {name} initialization failed: {e}")
	active.sort(key=lambda p: (_phase_order(p.phase), p.priority))
	_validate_unique_plugin_names(active)
	_validate_dependencies(active)
	return active


def _phase_order(phase: str) -> int:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把 phase 映射为排序顺序：pre < core < post。
	Map phase to ordering: pre < core < post.
	"""
	return {"pre": 0, "core": 1, "post": 2}.get(phase, 1)


def _validate_plugin_name(config_name: str, plugin: BasePlugin) -> None:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
插件声明名必须非空，并与 YAML key 一致。
	Plugin declared name must be non-empty and match the YAML key.
	"""
	if not plugin.name:
		raise PluginInitError(f"Plugin configured as {config_name} must declare a non-empty name")
	if plugin.name != config_name:
		raise PluginInitError(f"Plugin name mismatch: config key {config_name}, plugin declares {plugin.name}")


def _validate_unique_plugin_names(plugins: list[BasePlugin]) -> None:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
同一次加载中不允许出现同名插件。
	Duplicate plugin names are not allowed in one load.
	"""
	seen: set[str] = set()
	for plugin in plugins:
		if plugin.name in seen:
			raise PluginInitError(f"Duplicate plugin name loaded: {plugin.name}")
		seen.add(plugin.name)


def _validate_dependencies(plugins: list[BasePlugin]) -> None:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
使用 Kahn 算法做拓扑排序并检测环。
	Use Kahn's algorithm for topological sort and cycle detection.
	"""
	name_set = {p.name for p in plugins}
	name_to_plugin = {p.name: p for p in plugins}
	in_degree: dict[str, int] = {p.name: 0 for p in plugins}
	graph: dict[str, list[str]] = {p.name: [] for p in plugins}
	for plugin in plugins:
		for dep in plugin.depends_on:
			if dep not in name_set:
				if plugin.fail_closed:
					raise PluginInitError(f"Plugin {plugin.name} (fail_closed) depends on {dep} which is not loaded")
				logger.warning(f"Plugin {plugin.name} depends on {dep} which is not loaded")
				continue
			graph[dep].append(plugin.name)
			in_degree[plugin.name] += 1
	queue = [name for name, deg in in_degree.items() if deg == 0]
	sorted_names: list[str] = []
	while queue:
		queue.sort(key=lambda n: (_phase_order(name_to_plugin[n].phase), name_to_plugin[n].priority))
		node = queue.pop(0)
		sorted_names.append(node)
		for neighbor in graph[node]:
			in_degree[neighbor] -= 1
			if in_degree[neighbor] == 0:
				queue.append(neighbor)
	if len(sorted_names) != len(plugins):
		remaining = [p.name for p in plugins if p.name not in set(sorted_names)]
		raise PluginInitError(f"Circular plugin dependency detected among: {remaining}")
	plugins[:] = [name_to_plugin[n] for n in sorted_names]
