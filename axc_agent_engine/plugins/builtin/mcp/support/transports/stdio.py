"""MCP stdio JSON-RPC transport."""
import asyncio
import json
import logging
from typing import Any

from axc_agent_engine.plugins.builtin.mcp.support.models import MCPApplicationError, MCPTransportError
from axc_agent_engine.plugins.builtin.mcp.support.transports.base import merge_env

logger = logging.getLogger(__name__)


class JsonRpcStdioTransport:
	def __init__(
		self,
		command: str,
		args: list[str] | None = None,
		cwd: str = "",
		env: dict[str, str] | None = None,
		close_timeout: float = 5.0,
	) -> None:
		self.command = command
		self.args = args or []
		self.cwd = cwd
		self.env = env
		self.close_timeout = close_timeout
		self.process: asyncio.subprocess.Process | None = None
		self._request_id = 0
		self._pending_notifications: list[dict[str, Any]] = []
		self._stderr_task: asyncio.Task | None = None

	async def connect(self) -> None:
		if self.process:
			return
		if not self.command:
			raise ValueError("MCP stdio server requires command")
		self.process = await asyncio.create_subprocess_exec(
			self.command,
			*self.args,
			stdin=asyncio.subprocess.PIPE,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
			cwd=self.cwd or None,
			env=merge_env(self.env),
		)
		if self.process.stderr:
			self._stderr_task = asyncio.create_task(self._drain_stderr())

	async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
		if not self.process or not self.process.stdin or not self.process.stdout:
			raise MCPTransportError("MCP process is not running")
		self._request_id += 1
		request_id = self._request_id
		self.process.stdin.write(json.dumps({
			"jsonrpc": "2.0",
			"id": request_id,
			"method": method,
			"params": params or {},
		}, ensure_ascii=False).encode("utf-8") + b"\n")
		await self.process.stdin.drain()
		return await self._read_response(request_id)

	async def close(self) -> None:
		if not self.process:
			return
		self.process.terminate()
		try:
			await asyncio.wait_for(self.process.wait(), timeout=self.close_timeout)
		except asyncio.TimeoutError:
			self.process.kill()
			await self.process.wait()
		if self._stderr_task:
			self._stderr_task.cancel()
			self._stderr_task = None
		self.process = None

	async def _read_response(self, request_id: int) -> dict[str, Any]:
		if not self.process or not self.process.stdout:
			raise MCPTransportError("MCP process is not running")
		while True:
			line = await self.process.stdout.readline()
			if not line:
				raise MCPTransportError("MCP process closed")
			message = json.loads(line.decode("utf-8"))
			if "id" not in message:
				self._pending_notifications.append(message)
				continue
			if message.get("id") != request_id:
				continue
			if "error" in message:
				raise MCPApplicationError(f"MCP error: {message['error']}")
			return message.get("result", {})

	async def _drain_stderr(self) -> None:
		if not self.process or not self.process.stderr:
			return
		while True:
			line = await self.process.stderr.readline()
			if not line:
				return
			logger.debug("[mcp:%s stderr] %s", self.command, line.decode("utf-8", errors="replace").rstrip())
