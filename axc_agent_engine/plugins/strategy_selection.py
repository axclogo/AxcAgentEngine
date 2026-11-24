"""Resolve the agent's execution driver. 选择 Agent 的执行策略。"""

from axc_agent_engine.core.errors import PluginInitError
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.strategy import ExecutionStrategyPlugin


def select_strategy(plugins: list[BasePlugin], name: str, mode: str) -> tuple[ExecutionStrategyPlugin, str]:
	active = {p.name: p for p in plugins if isinstance(p, ExecutionStrategyPlugin)}
	if name:
		if name not in active:
			raise PluginInitError(f"Execution strategy {name} must be registered and enabled")
		return active[name], "explicit"
	if len(active) > 1:
		raise PluginInitError("Multiple execution strategies enabled; set runtime.strategy")
	if active:
		return next(iter(active.values())), "single_enabled"
	if mode == "react_only":
		from axc_agent_engine.plugins.strategies.react import ReActPlugin

		return ReActPlugin(), "runtime_routing"
	from axc_agent_engine.plugins.strategies.por import PORPlugin

	return PORPlugin(mode=mode), "runtime_routing"
