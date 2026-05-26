"""BuiltinTools plugin shell."""
from typing import Any, TYPE_CHECKING

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.tools.tool_output import ToolOutput

from .registry import ALL_TOOLS as _ALL_TOOLS
from .registry import DEFAULT_TOOLS as _DEFAULT_TOOLS

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext


class BuiltinToolsPlugin(BasePlugin):
	name = "builtin_tools"
	display_name = "内置工具"
	priority = 30
	version = "3.0.0"

	def initialize(self, config: dict, plugin_ctx: Any = None) -> None:  # type: ignore[override]
		self._load: list[str] = config.get("load", [])
		self._defer: list[str] = config.get("defer", [])
		self._plugin_ctx: Any = plugin_ctx

	def _active_deferred_for(self, exec_ctx: "ExecutionContext") -> set[str]:
		"""Return the currently active deferred tool names for this execution."""
		state = exec_ctx.get_plugin_state(self.name, lambda: {"active_deferred": set()})
		active = state.setdefault("active_deferred", set())
		if not isinstance(active, set):
			active = set(active)
			state["active_deferred"] = active
		return active

	async def on_execution_start(self, exec_ctx: "ExecutionContext") -> None:
		"""Initialize deferred tool state for the execution."""
		exec_ctx.get_plugin_state(self.name, lambda: {})["active_deferred"] = set()

	def get_tools(self) -> list[ToolDefinition]:
		tools: list[ToolDefinition] = []
		names_to_load = self._load if self._load else _DEFAULT_TOOLS
		for name in names_to_load:
			if name in _ALL_TOOLS:
				tool = _ALL_TOOLS[name]
				if name in self._defer:
					tools.append(ToolDefinition(
						name=tool.name,
						description=tool.description,
						parameters=tool.parameters,
						execute=tool.execute,
						is_read_only=tool.is_read_only,
						timeout=tool.timeout,
						deferred=True,
						capability=tool.capability,
						risk_level=tool.risk_level,
					))
				else:
					tools.append(tool)
		if self._defer:
			tools.append(ToolDefinition(
				name="tool_search",
				description="搜索可用的 deferred 工具，返回工具名称和描述。",
				parameters={"type": "object", "properties": {
					"query": {"type": "string", "description": "搜索关键词"}
				}, "required": ["query"]},
				execute=self._tool_search,
				is_read_only=True,
			))
		return tools

	def pre_llm_call(
		self,
		exec_ctx: "ExecutionContext",
		messages: list[dict],
		tools: list[dict] | None,
	) -> tuple[list[dict], list[dict] | None]:
		"""Dynamically inject activated deferred tool schemas."""
		active = self._active_deferred_for(exec_ctx)
		if not active or not tools:
			return messages, tools
		for name in active:
			if name in _ALL_TOOLS:
				tool = _ALL_TOOLS[name]
				schema = tool.to_openai_schema()
				if not any(t.get("function", {}).get("name") == name for t in tools):
					tools.append(schema)
		return messages, tools

	async def post_tool_call(
		self,
		exec_ctx: "ExecutionContext",
		tool_name: str,
		arguments: dict,
		result: "ToolOutput",
		duration_ms: int,
	) -> "ToolOutput":
		"""Deactivate a deferred tool after it was used."""
		active = self._active_deferred_for(exec_ctx)
		active.discard(tool_name)
		return result

	async def _tool_search(self, args: dict, context: dict) -> ToolOutput:
		"""Search and activate deferred tools."""
		query = args.get("query", "").lower()
		exec_ctx = context.get("exec_ctx")
		active = self._active_deferred_for(exec_ctx) if exec_ctx else set()
		results = []
		for name in self._defer:
			if name in _ALL_TOOLS:
				tool = _ALL_TOOLS[name]
				if query in name.lower() or query in tool.description.lower():
					active.add(name)
					results.append({"name": name, "description": tool.description})
		return ToolOutput.json_output(
			{"tools": results, "message": "工具已激活，可用于下一次调用" if results else "未找到匹配工具"},
			summary=f"找到 {len(results)} 个工具",
		)
