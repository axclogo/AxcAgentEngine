"""Validate plugin dependencies and manage initialization. 校验依赖并管理插件生命周期。"""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from axc_agent_engine.core.errors import PluginInitError
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.config_schema import normalize_plugin_config_schema, validate_plugin_config
from axc_agent_engine.plugins.context import PluginContext
from axc_agent_engine.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


def _prepare(configs: dict[str, dict], registry: PluginRegistry) -> list[BasePlugin]:
	active = []
	for name, config in configs.items():
		plugin = registry.create(name)
		if plugin is None:
			raise PluginInitError(f"Plugin {name} is configured but not registered")
		try:
			schema = normalize_plugin_config_schema(
				plugin.config_schema, plugin_name=name, display_name=plugin.display_name
			)
			validate_plugin_config(name, config, schema)
		except ValueError as exc:
			raise PluginInitError(f"Plugin {name} config validation failed: {exc}") from exc
		if config.get("enabled", False):
			active.append(plugin)
	_validate_dependencies(active)
	return active


async def close_plugins(plugins: list[BasePlugin]) -> None:
	"""Release dependants before their dependencies. 先关闭依赖方，再关闭被依赖的插件。"""
	for plugin in reversed(plugins):
		try:
			await asyncio.wait_for(plugin.close(), timeout=5.0)
		except asyncio.CancelledError:
			task = asyncio.current_task()
			if task is not None and task.cancelling():
				raise
			logger.warning(f"Plugin {plugin.name} cancelled its close")
		except TimeoutError:
			logger.warning(f"Plugin {plugin.name} close timed out")
		except Exception:
			logger.exception(f"Plugin {plugin.name} close failed")


@contextmanager
def initialized_plugins(
	configs: dict[str, dict], ctx: PluginContext, registry: PluginRegistry, pending: set[asyncio.Task] | None = None
) -> Iterator[list[BasePlugin]]:
	"""Roll back initialization and binding failures. 初始化或绑定失败时逆序释放资源。"""
	plugins = _prepare(configs, registry)
	started = []
	try:
		for plugin in plugins:
			started.append(plugin)
			try:
				plugin.initialize(configs[plugin.name], ctx)
			except Exception as exc:
				raise PluginInitError(f"Plugin {plugin.name} initialization failed: {exc}") from exc
		yield plugins
	except BaseException:
		try:
			loop = asyncio.get_running_loop()
		except RuntimeError:
			asyncio.run(close_plugins(started))
		else:
			tasks = pending if pending is not None else set()
			task = loop.create_task(close_plugins(started))
			tasks.add(task)
			task.add_done_callback(tasks.discard)
		raise


def load_plugins(plugins_config: dict[str, dict], ctx: PluginContext, registry: PluginRegistry) -> list[BasePlugin]:
	with initialized_plugins(plugins_config, ctx, registry) as plugins:
		return plugins


def _phase_order(phase: str) -> int:
	"""Order independent plugins by phase. 对无依赖关系的插件按阶段排序。"""
	return {"pre": 0, "core": 1, "post": 2}.get(phase, 1)


def _validate_dependencies(plugins: list[BasePlugin]) -> None:
	"""Sort dependencies before dependants and reject cycles. 将依赖排在调用方前并检测依赖环。"""
	name_to_plugin = {p.name: p for p in plugins}
	in_degree: dict[str, int] = {p.name: 0 for p in plugins}
	graph: dict[str, list[str]] = {p.name: [] for p in plugins}
	for plugin in plugins:
		for dep in plugin.depends_on:
			if dep not in name_to_plugin:
				raise PluginInitError(f"Plugin {plugin.name} depends on {dep} which is not loaded")
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
		remaining = [name for name, degree in in_degree.items() if degree]
		raise PluginInitError(f"Circular plugin dependency detected among: {remaining}")
	plugins[:] = [name_to_plugin[n] for n in sorted_names]
