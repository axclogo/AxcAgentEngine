"""Run example hosts with deterministic model responses. 使用离线模型验证示例宿主。"""

import runpy
from pathlib import Path

import pytest

from axc_agent_engine import AgentModels, EventType
from axc_agent_engine.core.context import ExecutionContext
from tests.planning.test_routing_integration import PLAN_CONTENT, RoutingProvider

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("0[1-5]_*/run.py")), ids=lambda path: path.parent.name)
async def test_official_example_host(path, monkeypatch, mock_llm, capsys):
	monkeypatch.syspath_prepend(str(path.parent))
	main = runpy.run_path(str(path))["main"]
	provider = RoutingProvider([{"content": "offline reply"}])
	model = mock_llm if path.parent.name.startswith("01") else provider
	monkeypatch.setitem(main.__globals__, "_agent_models", lambda: AgentModels(default=model))
	await main()
	assert ("hello" if model is mock_llm else "offline reply") in capsys.readouterr().out


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("0[6-7]_*/*.yaml")), ids=lambda path: path.stem)
async def test_sidecar_example_templates_select_official_auto(path, mock_llm):
	from axc_agent_engine import Engine, PluginRegistry
	from axc_agent_engine.plugins.builtin import PORPlugin

	registry = PluginRegistry()
	registry.register(PORPlugin)
	engine = Engine(plugin_registry=registry)
	try:
		template = engine.load_agent_template(str(path))
		assert template.config.runtime.strategy == "por"
		assert template.config.plugins["por"].model_dump()["mode"] == "auto"
		assert await template.instantiate(models=AgentModels(default=mock_llm)).chat("hello") == "hello"
	finally:
		await engine.close()


@pytest.mark.parametrize("strategy", ["single_reply", "por"])
async def test_custom_loop_example_selects_driver(strategy, monkeypatch):
	path = EXAMPLES / "08_custom_loop"
	monkeypatch.syspath_prepend(str(path))
	engine = runpy.run_path(str(path / "run.py"))["create_engine"]()
	provider = RoutingProvider([{"content": PLAN_CONTENT}, {"content": "step"}, {"content": "summary"}])
	try:
		template = engine.load_agent_template(str(path / "agent.yaml"))
		overrides = {"runtime.strategy": "por"} if strategy == "por" else None
		agent = template.instantiate(models=AgentModels(default=provider), overrides=overrides)
		events = [event async for event in agent.stream("example")]
		assert events[-1].type == EventType.DONE
		assert events[-1].content == (PLAN_CONTENT if strategy == "single_reply" else "summary")
		assert len(provider.requests) == (1 if strategy == "single_reply" else 3)
	finally:
		await engine.close()


async def test_example_stats_are_isolated_per_run(plugin_ctx):
	plugin = runpy.run_path(str(EXAMPLES / "05_custom_plugin" / "my_plugin.py"))["StatsPlugin"]()
	plugin.initialize({}, plugin_ctx)
	first, second = ExecutionContext(), ExecutionContext()
	await plugin.on_round_end(first, "", "", [{}])
	tool = plugin.get_tools()[0]
	assert (await tool.execute({}, {"exec_ctx": first})).content == {"total_tool_calls": 1}
	assert (await tool.execute({}, {"exec_ctx": second})).content == {"total_tool_calls": 0}
