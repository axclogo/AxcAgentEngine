"""Manage one official MCP client session. 管理一个官方 MCP 客户端会话。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from .models import MCPApplicationError, MCPTool, MCPTransportError
from .normalization import normalize_call_result


class MCPConnection:
	def __init__(self, config: dict[str, Any]) -> None:
		self.config = dict(config)
		self.name = config.get("name", "unnamed")
		if config.get("use_sdk") is False:
			raise ValueError("use_sdk: false is no longer supported; configure a standard MCP server")
		self._timeout = float(config.get("timeout", 60))
		self._lock = asyncio.Lock()
		self._owner: asyncio.Task | None = None
		self._session: Any = None
		self._stop = asyncio.Event()

	async def _own_session(self, ready: asyncio.Future) -> None:
		try:
			from httpx import AsyncClient
			from mcp import ClientSession, StdioServerParameters
			from mcp.client.sse import sse_client
			from mcp.client.stdio import stdio_client
			from mcp.client.streamable_http import streamable_http_client
		except ImportError:
			ready.set_exception(RuntimeError("Install axc-agent-engine[mcp] to use MCP"))
			return
		try:
			async with AsyncExitStack() as stack:
				config = self.config
				transport = config.get("transport") or ("stdio" if "command" in config else "http")
				if transport in {"stdio", "command"}:
					params = StdioServerParameters(
						command=config["command"],
						args=config.get("args", []),
						cwd=config.get("cwd"),
						env=config.get("env"),
					)
					streams = await stack.enter_async_context(stdio_client(params))
				elif transport == "sse":
					streams = await stack.enter_async_context(sse_client(config["url"], headers=config.get("headers")))
				elif transport in {"http", "streamable_http", "streamable-http"}:
					client = await stack.enter_async_context(
						AsyncClient(headers=config.get("headers"), timeout=self._timeout, follow_redirects=True)
					)
					streams = await stack.enter_async_context(streamable_http_client(config["url"], http_client=client))
				else:
					raise ValueError(f"Unsupported MCP transport {transport}")
				session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
				await session.initialize()
				ready.set_result(session)
				await self._stop.wait()
		except Exception as exc:
			if not ready.done():
				ready.set_exception(exc)
			else:
				raise

	async def _open(self) -> None:
		if self._session is not None and self._owner and not self._owner.done():
			return
		await self._close()
		ready = asyncio.get_running_loop().create_future()
		self._stop.clear()
		self._owner = asyncio.create_task(self._own_session(ready))
		try:
			async with asyncio.timeout(float(self.config.get("connect_timeout", 30))):
				self._session = await ready
		except BaseException:
			self._owner.cancel()
			await asyncio.gather(self._owner, return_exceptions=True)
			self._owner = None
			raise

	async def connect(self) -> None:
		async with self._lock:
			await self._open()

	async def _request(self, operation, timeout: float, retryable: bool):
		async with self._lock:
			for attempt in range(2 if retryable else 1):
				await self._open()
				from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
				from mcp.shared.exceptions import McpError

				try:
					async with asyncio.timeout(timeout):
						return await operation(self._session)
				except McpError as exc:
					raise MCPApplicationError(str(exc)) from exc
				except (OSError, TimeoutError, BrokenResourceError, ClosedResourceError, EndOfStream) as exc:
					await self._close()
					if attempt or not retryable:
						raise MCPTransportError(str(exc)) from exc

	async def list_tools(self) -> list[MCPTool]:
		async def collect(session):
			result = []
			cursor = None
			while True:
				page = await session.list_tools(cursor=cursor)
				for tool in page.tools:
					annotations = tool.annotations.model_dump(exclude_none=True) if tool.annotations else {}
					result.append(MCPTool(tool.name, tool.description or "", tool.inputSchema, annotations))
				cursor = page.nextCursor
				if not cursor:
					return result

		return await self._request(collect, float(self.config.get("list_timeout", self._timeout)), True)

	async def call_tool(self, tool_name: str, arguments: dict[str, Any], *, retryable: bool = False) -> Any:
		result = await self._request(
			lambda s: s.call_tool(tool_name, arguments),
			float(self.config.get("call_timeout", self._timeout)),
			retryable,
		)
		content = normalize_call_result(result.model_dump(by_alias=True, exclude_none=True))
		if result.isError:
			raise MCPApplicationError(str(content))
		return content

	async def _close(self) -> None:
		if self._owner:
			self._stop.set()
			try:
				await self._owner
			finally:
				self._owner = None
				self._session = None

	async def close(self) -> None:
		async with self._lock:
			await self._close()
