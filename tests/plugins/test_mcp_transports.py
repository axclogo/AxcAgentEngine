import sys
import types
from contextlib import asynccontextmanager

import pytest

from axc_agent_engine.plugins.builtin.mcp.support.models import MCPApplicationError
from axc_agent_engine.plugins.builtin.mcp.support.transports import build_transport
from axc_agent_engine.plugins.builtin.mcp.support.transports.base import (
	call_transport_client,
	client_session_class,
	merge_env,
	module_exists,
	timeout,
)
from axc_agent_engine.plugins.builtin.mcp.support.transports.http import JsonRpcHttpTransport
from axc_agent_engine.plugins.builtin.mcp.support.transports.sdk import OfficialSDKTransport


def test_transport_base_helpers(monkeypatch):
	assert timeout({"x": "2"}, "x", 1.0) == 2.0
	assert timeout({"x": "-1"}, "x", 1.0) == 1.0
	assert timeout({"x": "bad"}, "x", 1.0) == 1.0
	assert module_exists("sys")
	assert not module_exists("definitely_missing_axc_module")
	assert call_transport_client(lambda url, headers=None: (url, headers), "u", {"h": "v"}) == ("u", {"h": "v"})
	assert call_transport_client(lambda url: url, "u", {"h": "v"}) == "u"
	env = merge_env({"X": 1})
	assert env["X"] == "1"
	assert merge_env(None) is None


def test_client_session_class_fallback(monkeypatch):
	mcp = types.ModuleType("mcp")
	session_mod = types.ModuleType("mcp.client.session")
	class Session: pass
	session_mod.ClientSession = Session
	monkeypatch.setitem(sys.modules, "mcp", mcp)
	monkeypatch.setitem(sys.modules, "mcp.client.session", session_mod)
	assert client_session_class() is Session


def test_official_sdk_transport_available_branches(monkeypatch):
	monkeypatch.setattr("importlib.import_module", lambda name: object())
	monkeypatch.setattr("axc_agent_engine.plugins.builtin.mcp.support.transports.sdk.module_exists", lambda name: name.endswith("stdio"))
	assert OfficialSDKTransport.available({"transport": "command"})
	assert not OfficialSDKTransport.available({"transport": "sse"})

	def missing(name):
		raise ImportError(name)

	monkeypatch.setattr("importlib.import_module", missing)
	assert not OfficialSDKTransport.available({"transport": "stdio"})


async def test_json_rpc_http_transport_success_error_and_close(monkeypatch):
	class Response:
		def __init__(self, payload):
			self.payload = payload
		def raise_for_status(self):
			return None
		def json(self):
			return self.payload
	class Client:
		def __init__(self, timeout=None, headers=None):
			self.closed = False
			self.payloads = [Response({"result": {"ok": True}}), Response({"error": {"message": "bad"}})]
		async def post(self, url, json):
			self.last = (url, json)
			return self.payloads.pop(0)
		async def aclose(self):
			self.closed = True
	class Httpx:
		AsyncClient = Client
	monkeypatch.setitem(sys.modules, "httpx", Httpx)
	transport = JsonRpcHttpTransport("http://mcp", headers={"a": "b"})
	await transport.connect()
	result = await transport.request("tools/list")
	assert result == {"ok": True}
	with pytest.raises(MCPApplicationError):
		await transport.request("tools/list")
	client = transport._client
	await transport.close()
	assert client.closed is True
	with pytest.raises(ValueError):
		await JsonRpcHttpTransport("").connect()


def test_build_transport_selects_http_and_rejects_empty(monkeypatch):
	monkeypatch.setattr(OfficialSDKTransport, "available", classmethod(lambda cls, config: False))
	assert isinstance(build_transport({"url": "http://x"}), JsonRpcHttpTransport)
	with pytest.raises(ValueError):
		build_transport({})


async def test_official_sdk_transport_request_paths(monkeypatch):
	class Tool:
		name = "tool"
		description = "desc"
		inputSchema = {"type": "object"}
	class ListResult:
		tools = [Tool()]
	class CallResult:
		content = []
		isError = False
	class Session:
		def __init__(self):
			self.initialized = False
		async def initialize(self):
			self.initialized = True
		async def list_tools(self):
			return ListResult()
		async def call_tool(self, name, arguments):
			self.called = (name, arguments)
			return CallResult()
	transport = OfficialSDKTransport({"transport": "stdio", "command": "x"})
	transport._session = Session()
	assert await transport.request("initialize") == {}
	assert await transport.request("notifications/initialized") == {}
	assert (await transport.request("tools/list"))["tools"][0]["name"] == "tool"
	assert "content" in await transport.request("tools/call", {"name": "tool", "arguments": {"a": 1}})
	with pytest.raises(RuntimeError):
		await transport.request("unknown")
	await transport.close()
	assert transport._session is None


async def test_official_sdk_transport_open_streams_and_connect_errors(monkeypatch):
	entered = []

	class DummySession:
		def __init__(self, read_stream, write_stream):
			self.streams = (read_stream, write_stream)

		async def __aenter__(self):
			return self

		async def __aexit__(self, *args):
			return False

		async def initialize(self):
			return None

	@asynccontextmanager
	async def ctx(value):
		entered.append(value)
		yield value

	class ParamsWithCwd:
		def __init__(self, **kwargs):
			self.kwargs = kwargs

	class ParamsNoCwd:
		def __init__(self, **kwargs):
			if "cwd" in kwargs:
				raise TypeError("cwd unsupported")
			self.kwargs = kwargs

	stdio_mod = types.SimpleNamespace(
		StdioServerParameters=ParamsWithCwd,
		stdio_client=lambda params: ctx(("stdio-read", params.kwargs)),
	)
	sse_mod = types.SimpleNamespace(sse_client=lambda url, headers=None: ctx(("sse-read", "sse-write")))
	http_mod = types.SimpleNamespace(streamable_http_client=lambda url, headers=None: ctx(("http-read", "http-write")))

	def fake_import(name):
		if name == "mcp.client.stdio":
			return stdio_mod
		if name == "mcp.client.sse":
			return sse_mod
		if name == "mcp.client.streamable_http":
			return http_mod
		raise ImportError(name)

	monkeypatch.setattr("axc_agent_engine.plugins.builtin.mcp.support.transports.sdk.importlib.import_module", fake_import)
	monkeypatch.setattr("axc_agent_engine.plugins.builtin.mcp.support.transports.sdk.client_session_class", lambda: DummySession)

	stdio = OfficialSDKTransport({"transport": "stdio", "command": "cmd", "args": ["a"], "env": {"X": "1"}, "cwd": "/tmp"})
	await stdio.connect()
	assert stdio._session.streams[0] == "stdio-read"
	assert "cwd" in stdio._session.streams[1]
	await stdio.connect()
	await stdio.close()

	stdio_mod.StdioServerParameters = ParamsNoCwd
	no_cwd = OfficialSDKTransport({"transport": "command", "command": "cmd"})
	await no_cwd.connect()
	assert "cwd" not in no_cwd._session.streams[1]
	await no_cwd.close()

	sse = OfficialSDKTransport({"transport": "sse", "url": "http://sse", "headers": {"h": "v"}})
	await sse.connect()
	assert sse._session.streams[0] == "sse-read"
	await sse.close()

	http = OfficialSDKTransport({"transport": "http", "url": "http://http"})
	await http.connect()
	assert http._session.streams[0] == "http-read"
	await http.close()

	with pytest.raises(ValueError):
		await OfficialSDKTransport({"transport": "bad"})._open_streams(types.SimpleNamespace())

	@asynccontextmanager
	async def broken_ctx(value):
		raise RuntimeError("open failed")
		yield value

	stdio_mod.stdio_client = lambda params: broken_ctx(("x", "y"))
	broken = OfficialSDKTransport({"transport": "stdio", "command": "cmd"})
	with pytest.raises(RuntimeError):
		await broken.connect()
	assert broken._session is None
