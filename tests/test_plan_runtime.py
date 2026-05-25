"""Tests for PlanRuntime and PORRunner."""
from unittest.mock import MagicMock
from axc_agent_engine.planning.runtime import PlanRuntime
from axc_agent_engine.planning.por_runner import PORRunner
from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.tools.registry import ToolRegistry


class TestPlanRuntime:
	def test_dataclass_fields(self):
		llm = MagicMock()
		ms = MessageStore()
		reg = ToolRegistry()
		pm = PluginManager([])
		ctx = ExecutionContext()
		rt = PlanRuntime(llm_caller=llm, message_store=ms, registry=reg, plugin_manager=pm, ctx=ctx)
		assert rt.llm_caller is llm
		assert rt.message_store is ms
		assert rt.registry is reg
		assert rt.plugin_manager is pm
		assert rt.ctx is ctx

	def test_por_runner_with_runtime(self):
		llm = MagicMock()
		ms = MessageStore()
		reg = ToolRegistry()
		pm = PluginManager([])
		ctx = ExecutionContext()
		rt = PlanRuntime(llm_caller=llm, message_store=ms, registry=reg, plugin_manager=pm, ctx=ctx)
		runner = PORRunner(runtime=rt)
		assert runner._llm is llm
		assert runner._messages is ms
		assert runner._registry is reg
		assert runner._pm is pm
		assert runner._ctx is ctx
