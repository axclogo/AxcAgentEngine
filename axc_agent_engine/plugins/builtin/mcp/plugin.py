"""MCP plugin - connects Model Context Protocol servers as optional tools.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.common import bounded_int, externalize_text, artifact_store_from_context, strict_bounded_int
from axc_agent_engine.plugins.builtin.config_schemas import MCP_CONFIG_SCHEMA
from axc_agent_engine.plugins.builtin.mcp.support import MCPConnection, MCPTool
from axc_agent_engine.core.schema import ToolDefinition

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext

logger = logging.getLogger(__name__)


class MCPToolFactory:
	def __init__(self, plugin: "MCPPlugin") -> None:
		self.plugin = plugin

	def to_tool_definition(self, server_name: str, tool: MCPTool) -> ToolDefinition:
		plugin = self.plugin
		tool_name = f"mcp.{server_name}.{tool.name}"
		override = plugin._tool_override(tool_name, server_name, tool.name)
		read_only = bool(override.get("read_only", _read_only_from_annotations(tool.annotations, plugin._default_read_only)))
		risk_level = str(override.get("risk_level", _risk_from_annotations(tool.annotations, plugin._default_risk_level)))
		capability = str(override.get("capability", plugin._default_capability))
		timeout = strict_bounded_int(override.get("timeout", 120), 1, 3600, f"mcp.tool_overrides.{tool_name}.timeout")
		return ToolDefinition(
			name=tool_name,
			description=tool.description,
			parameters=tool.input_schema,
			is_read_only=read_only,
			timeout=timeout,
			capability=capability,
			risk_level=risk_level,
			execute=plugin._make_execute_fn(server_name, tool.name),
		)


class MCPDiscoveryService:
	def __init__(self, plugin: "MCPPlugin") -> None:
		self.plugin = plugin

	async def initialize(self) -> None:
		plugin = self.plugin
		plugin._tools = []
		seen_tool_names: set[str] = set()
		for server_config in plugin._servers:
			name = server_config.get("name", "unnamed")
			start = time.time()
			server_tools: list[ToolDefinition] = []
			conn: MCPConnection | None = None
			try:
				conn = MCPConnection(server_config)
				await conn.connect()
				for tool in await conn.list_tools():
					tool_definition = plugin._to_tool_definition(name, tool)
					if not plugin._tool_allowed(tool_definition.name):
						continue
					duplicate_in_batch = any(existing.name == tool_definition.name for existing in server_tools)
					if tool_definition.name in seen_tool_names or duplicate_in_batch:
						raise RuntimeError(f"Duplicate MCP tool name: {tool_definition.name}")
					server_tools.append(tool_definition)
				for tool_definition in server_tools:
					seen_tool_names.add(tool_definition.name)
				plugin._tools.extend(server_tools)
				plugin._connections[name] = conn
				plugin._health[name] = {
					"connected": True,
					"tools": len(server_tools),
					"latency_ms": int((time.time() - start) * 1000),
					"error": "",
				}
				logger.info("[mcp] connected to %s", name)
			except Exception as e:
				if conn:
					try:
						await conn.close()
					except Exception:
						logger.debug("[mcp] failed to close rejected connection %s", name, exc_info=True)
				plugin._health[name] = {
					"connected": False,
					"tools": 0,
					"latency_ms": int((time.time() - start) * 1000),
					"error": str(e),
				}
				raise
		plugin._initialized = True


class MCPOutputPresenter:
	def __init__(self, plugin: "MCPPlugin") -> None:
		self.plugin = plugin

	async def externalize_large_output(self, output, context: dict):
		plugin = self.plugin
		content, ref = await externalize_text(
			output._content_as_str(),
			artifact_store_from_context(context),
			plugin._max_result_bytes,
			{"source": "mcp", "content_type": output.content_type},
			logger,
			"mcp",
		)
		if ref is None:
			return output
		output.artifacts.append(ref)
		output.content = content
		output.content_type = "json"
		return output


class MCPPlugin(BasePlugin):
	name = "mcp"
	display_name = "MCP 工具"
	priority = 30
	version = "2.0.0"
	config_schema = MCP_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		super().initialize(config, plugin_ctx)
		self._servers = config.get("servers", [])
		self._allowed_tools = set(config.get("allowed_tools", []))
		self._denied_tools = set(config.get("denied_tools", []))
		self._tool_overrides = config.get("tool_overrides", {})
		self._default_capability = str(config.get("capability", ""))
		self._default_risk_level = str(config.get("risk_level", "moderate"))
		self._default_read_only = bool(config.get("read_only", False))
		self._max_result_bytes = strict_bounded_int(
			config.get("max_result_bytes", 512_000),
			1,
			10 * 1024 * 1024,
			"mcp.max_result_bytes",
		)
		self._connections: dict[str, MCPConnection] = {}
		self._tools: list[ToolDefinition] = []
		self._health: dict[str, dict[str, Any]] = {}
		self._initialized = False
		self._tool_factory = MCPToolFactory(self)
		self._discovery = MCPDiscoveryService(self)
		self._presenter = MCPOutputPresenter(self)

	def get_tools(self) -> list[ToolDefinition]:
		return self._tools

	async def on_execution_start(self, exec_ctx: "ExecutionContext") -> None:
		if self._initialized or not self._servers:
			return
		await self._async_initialize()
		registry = self._plugin_ctx.tool_registry if self._plugin_ctx else None
		if registry and self._tools:
			registry.register_late_many(self._tools, plugin_name=self.name, reason="mcp_tool_discovery")
			logger.info("[mcp] registered %s tools", len(self._tools))

	async def close(self) -> None:
		for name, conn in list(self._connections.items()):
			try:
				await conn.close()
			except Exception as e:
				logger.warning("[mcp] failed to close %s: %s", name, e)
		self._connections.clear()
		self._health.clear()
		self._initialized = False

	async def _async_initialize(self) -> None:
		await self._discovery.initialize()

	def _to_tool_definition(self, server_name: str, tool: MCPTool) -> ToolDefinition:
		return self._tool_factory.to_tool_definition(server_name, tool)

	def _make_execute_fn(self, server_name: str, tool_name: str):
		async def _execute(args: dict, context: dict):
			from axc_agent_engine.tools.tool_output import ToolOutput

			conn = self._connections.get(server_name)
			if not conn:
				return ToolOutput.error(f"MCP Server '{server_name}' 未连接")
			full_name = f"mcp.{server_name}.{tool_name}"
			override = self._tool_override(full_name, server_name, tool_name)
			retryable = bool(override.get("retryable", False))
			start = time.time()
			try:
				result: Any = await conn.call_tool(tool_name, args, retryable=retryable)
				duration_ms = int((time.time() - start) * 1000)
				if isinstance(result, str):
					output = ToolOutput.text(result, summary=f"mcp:{server_name}.{tool_name} 返回文本")
				else:
					output = ToolOutput.json_output(result, summary=f"mcp:{server_name}.{tool_name} 返回 JSON")
				output.metadata.update({"server": server_name, "tool": tool_name, "duration_ms": duration_ms})
				return await self._externalize_large_output(output, context)
			except Exception as e:
				return ToolOutput.error(str(e))

		return _execute

	def _tool_allowed(self, tool_name: str) -> bool:
		keys = _tool_keys(tool_name)
		if self._allowed_tools and not (self._allowed_tools & keys):
			return False
		return not (self._denied_tools & keys)

	def _tool_override(self, full_name: str, server_name: str, tool_name: str) -> dict[str, Any]:
		override = self._tool_overrides.get(full_name)
		if override is None:
			override = self._tool_overrides.get(f"{server_name}.{tool_name}")
		if override is None:
			override = self._tool_overrides.get(tool_name)
		return override if isinstance(override, dict) else {}

	async def _externalize_large_output(self, output, context: dict):
		return await self._presenter.externalize_large_output(output, context)

	def health(self) -> dict[str, dict[str, Any]]:
		return dict(self._health)


def _read_only_from_annotations(annotations: dict[str, Any], default: bool) -> bool:
	if not isinstance(annotations, dict):
		return default
	if "readOnlyHint" in annotations:
		return bool(annotations.get("readOnlyHint"))
	return default


def _risk_from_annotations(annotations: dict[str, Any], default: str) -> str:
	if not isinstance(annotations, dict):
		return default
	if annotations.get("destructiveHint") or annotations.get("openWorldHint"):
		return "dangerous"
	if annotations.get("readOnlyHint"):
		return "safe"
	return default


def _tool_keys(full_name: str) -> set[str]:
	parts = full_name.split(".")
	if len(parts) >= 3 and parts[0] == "mcp":
		return {full_name, ".".join(parts[1:]), parts[-1]}
	return {full_name}
