"""MCP transport factory and compatibility exports."""
from typing import Any

from axc_agent_engine.plugins.builtin.mcp.support.models import MCPTransport
from axc_agent_engine.plugins.builtin.mcp.support.transports.base import timeout
from axc_agent_engine.plugins.builtin.mcp.support.transports.http import JsonRpcHttpTransport
from axc_agent_engine.plugins.builtin.mcp.support.transports.sdk import OfficialSDKTransport
from axc_agent_engine.plugins.builtin.mcp.support.transports.stdio import JsonRpcStdioTransport


def build_transport(config: dict[str, Any]) -> MCPTransport:
	transport = config.get("transport", "")
	if config.get("use_sdk", True) and OfficialSDKTransport.available(config):
		return OfficialSDKTransport(config)
	if transport in {"stdio", "command"} or "command" in config:
		return JsonRpcStdioTransport(
			config.get("command", ""),
			config.get("args", []),
			cwd=config.get("cwd", ""),
			env=config.get("env"),
			close_timeout=timeout(config, "close_timeout", 5.0),
		)
	if transport in {"http", "streamable_http", "sse"} or "url" in config:
		return JsonRpcHttpTransport(
			config.get("url", ""),
			headers=config.get("headers"),
			timeout=timeout(config, "timeout", 60.0),
		)
	raise ValueError("MCP server config requires command or url")


__all__ = [
	"JsonRpcHttpTransport",
	"JsonRpcStdioTransport",
	"OfficialSDKTransport",
	"build_transport",
	"timeout",
]
