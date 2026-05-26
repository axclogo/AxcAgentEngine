"""Official MCP Python SDK transport adapter.
中文：此文档说明相关引擎组件的行为。"""
import importlib
from contextlib import AsyncExitStack
from typing import Any

from axc_agent_engine.plugins.builtin.mcp.support.normalization import sdk_call_result_to_dict, sdk_tool_to_dict
from axc_agent_engine.plugins.builtin.mcp.support.transports.base import (
	call_transport_client,
	client_session_class,
	module_exists,
)


class OfficialSDKTransport:
	"""Official MCP Python SDK transport adapter with JSON-RPC fallback kept outside.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, config: dict[str, Any]) -> None:
		self.config = dict(config)
		self.transport = self.config.get("transport", "")
		self._exit_stack: AsyncExitStack | None = None
		self._session: Any = None
		self._initialized = False

	@classmethod
	def available(cls, config: dict[str, Any]) -> bool:
		try:
			importlib.import_module("mcp")
			importlib.import_module("mcp.client.session")
		except ImportError:
			return False
		transport = config.get("transport", "")
		if transport in {"stdio", "command"} or "command" in config:
			return module_exists("mcp.client.stdio")
		if transport == "sse":
			return module_exists("mcp.client.sse")
		if transport in {"streamable-http", "streamable_http", "http"} or "url" in config:
			return module_exists("mcp.client.streamable_http")
		return False

	async def connect(self) -> None:
		if self._session:
			return
		self._exit_stack = AsyncExitStack()
		try:
			read_stream, write_stream = await self._open_streams(self._exit_stack)
			session_cls = client_session_class()
			self._session = await self._exit_stack.enter_async_context(session_cls(read_stream, write_stream))
		except Exception:
			if self._exit_stack:
				await self._exit_stack.aclose()
			self._exit_stack = None
			self._session = None
			raise

	async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
		if not self._session:
			await self.connect()
		if method == "initialize":
			if not self._initialized:
				await self._session.initialize()
				self._initialized = True
			return {}
		if method == "notifications/initialized":
			return {}
		if method == "tools/list":
			result = await self._session.list_tools()
			return {"tools": [sdk_tool_to_dict(tool) for tool in getattr(result, "tools", [])]}
		if method == "tools/call":
			params = params or {}
			result = await self._session.call_tool(params.get("name", ""), params.get("arguments") or {})
			return sdk_call_result_to_dict(result)
		raise RuntimeError(f"Unsupported MCP SDK method: {method}")

	async def close(self) -> None:
		if self._exit_stack:
			await self._exit_stack.aclose()
		self._exit_stack = None
		self._session = None
		self._initialized = False

	async def _open_streams(self, stack: AsyncExitStack) -> tuple[Any, Any]:
		transport = self.transport
		if transport in {"stdio", "command"} or "command" in self.config:
			stdio = importlib.import_module("mcp.client.stdio")
			params_cls = getattr(stdio, "StdioServerParameters")
			stdio_client = getattr(stdio, "stdio_client")
			params_kwargs = {
				"command": self.config.get("command", ""),
				"args": self.config.get("args", []),
				"env": self.config.get("env"),
			}
			try:
				params = params_cls(**params_kwargs, cwd=self.config.get("cwd") or None)
			except TypeError:
				params = params_cls(**params_kwargs)
			return await stack.enter_async_context(stdio_client(params))
		if transport == "sse":
			sse = importlib.import_module("mcp.client.sse")
			sse_client = getattr(sse, "sse_client")
			return await stack.enter_async_context(call_transport_client(
				sse_client,
				self.config.get("url", ""),
				headers=self.config.get("headers"),
			))
		if transport in {"streamable-http", "streamable_http", "http"} or "url" in self.config:
			http = importlib.import_module("mcp.client.streamable_http")
			client = getattr(http, "streamablehttp_client", None) or getattr(http, "streamable_http_client")
			return await stack.enter_async_context(call_transport_client(
				client,
				self.config.get("url", ""),
				headers=self.config.get("headers"),
			))
		raise ValueError("Unsupported MCP SDK transport")
