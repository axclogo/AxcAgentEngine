import pytest

from axc_agent_engine.plugins.builtin.mcp.support import MCPConnection


def test_rejects_removed_transport():
	with pytest.raises(ValueError, match="standard MCP"):
		MCPConnection({"use_sdk": False})


@pytest.mark.parametrize("transport,path", [("sse", "/sse"), ("streamable_http", "/mcp")])
async def test_real_official_transports_handle_calls_timeout_cancellation_and_close(transport, path):
	import asyncio
	import socket
	from contextlib import suppress

	import uvicorn
	from mcp.server.fastmcp import FastMCP

	from axc_agent_engine.plugins.builtin.mcp.support.models import MCPTransportError

	server = FastMCP("release")
	entered = asyncio.Event()
	release = asyncio.Event()

	@server.tool()
	async def echo(text: str) -> str:
		if text == "wait":
			entered.set()
			await release.wait()
		return text

	app = server.sse_app() if transport == "sse" else server.streamable_http_app()
	with socket.socket() as sock:
		sock.bind(("127.0.0.1", 0))
		port = sock.getsockname()[1]
		http = uvicorn.Server(uvicorn.Config(app, log_level="error"))
		task = asyncio.create_task(http.serve(sockets=[sock]))
		conn = MCPConnection({"transport": transport, "url": f"http://127.0.0.1:{port}{path}", "call_timeout": 1})
		try:
			async with asyncio.timeout(10):
				while not http.started:
					await asyncio.sleep(0.01)
				assert [tool.name for tool in await conn.list_tools()] == ["echo"]
				assert await conn.call_tool("echo", {"text": "ok"}) == "ok"
				waiting = asyncio.create_task(conn.call_tool("echo", {"text": "wait"}))
				await entered.wait()
				waiting.cancel()
				with pytest.raises(asyncio.CancelledError):
					await waiting
				with pytest.raises(MCPTransportError):
					await conn.call_tool("echo", {"text": "wait"})
				release.set()
				assert await conn.call_tool("echo", {"text": "reconnected"}) == "reconnected"
		finally:
			release.set()
			await conn.close()
			http.should_exit = True
			with suppress(asyncio.CancelledError):
				await asyncio.wait_for(task, 10)
