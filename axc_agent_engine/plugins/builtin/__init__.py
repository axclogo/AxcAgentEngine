"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
内置插件命名空间。
Builtin plugin namespace.

内置插件不会自动注册；宿主必须把需要的插件类注册到 Engine 的 PluginRegistry。
Builtin plugins are not registered automatically; hosts must register selected
plugin classes into the Engine PluginRegistry."""

from axc_agent_engine.plugins.builtin.builtin_tools.plugin import BuiltinToolsPlugin
from axc_agent_engine.plugins.builtin.collaboration.plugin import CollaborationPlugin
from axc_agent_engine.plugins.builtin.compress.plugin import CompressPlugin
from axc_agent_engine.plugins.builtin.cost_statistics.plugin import CostStatisticsPlugin
from axc_agent_engine.plugins.builtin.graph.plugin import GraphPlugin
from axc_agent_engine.plugins.builtin.hooks.plugin import HooksPlugin
from axc_agent_engine.plugins.builtin.human_in_the_loop.plugin import HumanInTheLoopPlugin
from axc_agent_engine.plugins.builtin.knowledge.plugin import KnowledgePlugin
from axc_agent_engine.plugins.builtin.mcp.plugin import MCPPlugin
from axc_agent_engine.plugins.builtin.memory.plugin import MemoryPlugin
from axc_agent_engine.plugins.builtin.output_format.plugin import OutputFormatPlugin
from axc_agent_engine.plugins.builtin.reflexion.plugin import ReflexionPlugin
from axc_agent_engine.plugins.builtin.repetition_guard.plugin import RepetitionGuardPlugin
from axc_agent_engine.plugins.builtin.risk_guard.plugin import RiskGuardPlugin
from axc_agent_engine.plugins.builtin.safety.plugin import SafetyPlugin
from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin
from axc_agent_engine.plugins.builtin.swarm.plugin import SwarmPlugin
from axc_agent_engine.plugins.builtin.tracing.plugin import TracingPlugin
from axc_agent_engine.plugins.builtin.registry import AVAILABLE_BUILTIN_PLUGINS

__all__ = [
	"AVAILABLE_BUILTIN_PLUGINS",
	"BuiltinToolsPlugin",
	"CollaborationPlugin",
	"CompressPlugin",
	"CostStatisticsPlugin",
	"GraphPlugin",
	"HooksPlugin",
	"HumanInTheLoopPlugin",
	"KnowledgePlugin",
	"MCPPlugin",
	"MemoryPlugin",
	"OutputFormatPlugin",
	"ReflexionPlugin",
	"RepetitionGuardPlugin",
	"RiskGuardPlugin",
	"SafetyPlugin",
	"SkillPlugin",
	"SwarmPlugin",
	"TracingPlugin",
]
