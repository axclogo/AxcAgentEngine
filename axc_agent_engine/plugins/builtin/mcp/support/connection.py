"""Connection owner for a single MCP server."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .models import DEFAULT_PROTOCOL_VERSION, MCPApplicationError, MCPTool, MCPTransport, MCPTransportError
from .normalization import normalize_call_result, tool_from_payload
from .transports import build_transport, timeout

logger = logging.getLogger(__name__)


class MCPConnection:
	"""Connection owner for a single MCP server."""

	def __init__(self, config: dict[str, Any]) -> None:
		self.config = dict(config)
		self.name = self.config.get("name", "unnamed")
		self._transport: MCPTransport = build_transport(self.config)
		self._connected = False
		self._request_lock = asyncio.Lock()
		self._connect_timeout = timeout(self.config, "connect_timeout", 30.0)
		self._list_timeout = timeout(self.config, "list_timeout", timeout(self.config, "timeout", 30.0))
		self._call_timeout = timeout(self.config, "call_timeout", timeout(self.config, "timeout", 60.0))

	async def connect(self) -> None:
		if self._connected:
			return
		async with self._request_lock:
			if self._connected:
				return
			await asyncio.wait_for(self._transport.connect(), timeout=self._connect_timeout)
			await asyncio.wait_for(self._transport.request("initialize", {
				"protocolVersion": self.config.get("protocol_version", DEFAULT_PROTOCOL_VERSION),
				"capabilities": {},
				"clientInfo": {"name": "axc_agent_engine", "version": "2.0"},
			}), timeout=self._connect_timeout)
			try:
				await asyncio.wait_for(self._transport.request("notifications/initialized", {}), timeout=self._connect_timeout)
			except Exception:
				logger.debug("[mcp] initialized notification not accepted by %s", self.name, exc_info=True)
			self._connected = True

	async def list_tools(self) -> list[MCPTool]:
		result = await self._request_with_reconnect("tools/list", {}, timeout=self._list_timeout, retryable=True)
		return [tool_from_payload(tool) for tool in result.get("tools", [])]

	async def call_tool(self, tool_name: str, arguments: dict[str, Any], *, retryable: bool = False) -> Any:
		result = await self._request_with_reconnect(
			"tools/call",
			{"name": tool_name, "arguments": arguments},
			timeout=self._call_timeout,
			retryable=retryable,
		)
		return normalize_call_result(result)

	async def close(self) -> None:
		await self._transport.close()
		self._connected = False

	async def _request_with_reconnect(
		self,
		method: str,
		params: dict[str, Any],
		*,
		timeout: float,
		retryable: bool,
	) -> dict[str, Any]:
		if not self._connected and method != "initialize":
			await self.connect()
		try:
			async with self._request_lock:
				return await asyncio.wait_for(self._transport.request(method, params), timeout=timeout)
		except MCPApplicationError:
			raise
		except (BrokenPipeError, ConnectionResetError, MCPTransportError, asyncio.TimeoutError):
			await self.close()
			if not retryable:
				raise
			logger.warning("[mcp] request %s failed, reconnecting server %s", method, self.name)
			await self.connect()
			async with self._request_lock:
				return await asyncio.wait_for(self._transport.request(method, params), timeout=timeout)
