"""MCP HTTP JSON-RPC transport."""
from typing import Any

from axc_agent_engine.plugins.builtin.mcp.support.models import MCPApplicationError


class JsonRpcHttpTransport:
	def __init__(self, url: str, headers: dict[str, str] | None = None, timeout: float = 60.0) -> None:
		self.url = url
		self.headers = headers or {}
		self.timeout = timeout
		self._client: Any = None
		self._request_id = 0

	async def connect(self) -> None:
		if self._client:
			return
		if not self.url:
			raise ValueError("MCP HTTP server requires url")
		import httpx
		self._client = httpx.AsyncClient(timeout=self.timeout, headers=self.headers)

	async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
		if not self._client:
			await self.connect()
		self._request_id += 1
		response = await self._client.post(self.url, json={
			"jsonrpc": "2.0",
			"id": self._request_id,
			"method": method,
			"params": params or {},
		})
		response.raise_for_status()
		payload = response.json()
		if "error" in payload:
			raise MCPApplicationError(f"MCP error: {payload['error']}")
		return payload.get("result", {})

	async def close(self) -> None:
		if self._client:
			await self._client.aclose()
			self._client = None
