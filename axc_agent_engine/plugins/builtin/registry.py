"""Builtin plugin registry.
中文：内置插件注册表。"""
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

AVAILABLE_BUILTIN_PLUGINS = {
	"builtin_tools": BuiltinToolsPlugin,
	"collaboration": CollaborationPlugin,
	"compress": CompressPlugin,
	"cost_statistics": CostStatisticsPlugin,
	"graph": GraphPlugin,
	"hooks": HooksPlugin,
	"human_in_the_loop": HumanInTheLoopPlugin,
	"knowledge": KnowledgePlugin,
	"mcp": MCPPlugin,
	"memory": MemoryPlugin,
	"output_format": OutputFormatPlugin,
	"reflexion": ReflexionPlugin,
	"repetition_guard": RepetitionGuardPlugin,
	"risk_guard": RiskGuardPlugin,
	"safety": SafetyPlugin,
	"skill": SkillPlugin,
	"swarm": SwarmPlugin,
	"tracing": TracingPlugin,
}
