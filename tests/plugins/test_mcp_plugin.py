"""Tests for the MCP plugin support layer."""
from __future__ import annotations

import sys
import asyncio

from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.mcp.plugin import MCPPlugin
from axc_agent_engine.plugins.builtin.mcp.support.client import MCPConnection, normalize_call_result
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.storage.artifact_store import InMemoryArtifactStore


SERVER_CODE = r"""
import json
import sys

for line in sys.stdin:
    req = json.loads(line)
    method = req.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}}
    elif method == "notifications/initialized":
        result = {}
    elif method == "tools/list":
        result = {"tools": [
            {"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}, "annotations": {"readOnlyHint": True}},
            {"name": "write", "description": "Write", "inputSchema": {"type": "object", "properties": {}}, "annotations": {"destructiveHint": True}},
            {"name": "large", "description": "Large", "inputSchema": {"type": "object", "properties": {}}},
        ]}
    elif method == "tools/call":
        name = req.get("params", {}).get("name", "")
        text = req.get("params", {}).get("arguments", {}).get("text", "")
        if name == "large":
            result = {"content": [{"type": "text", "text": "x" * 200}]}
        else:
            result = {"content": [{"type": "text", "text": name + ":" + text}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result}), flush=True)
"""


async def test_mcp_connection_lists_and_calls_stdio_tool():
    conn = MCPConnection({"name": "local", "command": sys.executable, "args": ["-c", SERVER_CODE]})
    try:
        await conn.connect()
        tools = await conn.list_tools()
        result = await conn.call_tool("echo", {"text": "ok"})
    finally:
        await conn.close()

    assert tools[0].name == "echo"
    assert result == "echo:ok"


async def test_mcp_plugin_registers_namespaced_tools():
    ctx = PluginContext()
    ctx.tool_registry = ToolRegistry()
    plugin = MCPPlugin()
    plugin.initialize({
        "servers": [{"name": "local", "command": sys.executable, "args": ["-c", SERVER_CODE]}],
    }, ctx)
    try:
        await plugin.on_execution_start(None)
        tools = plugin.get_tools()
        echo = next(tool for tool in tools if tool.name == "mcp.local.echo")
        result = await echo.execute({"text": "ok"}, {})
    finally:
        await plugin.close()

    assert echo.name == "mcp.local.echo"
    assert ctx.tool_registry.get("mcp.local.echo") is not None
    assert result.content == "echo:ok"


def test_normalize_call_result_preserves_non_text_content():
    result = normalize_call_result({"content": [{"type": "image", "data": "x"}]})

    assert result == [{"type": "image", "data": "x"}]


async def test_mcp_plugin_initialization_is_idempotent():
    ctx = PluginContext()
    ctx.tool_registry = ToolRegistry()
    plugin = MCPPlugin()
    plugin.initialize({
        "servers": [{"name": "local", "command": sys.executable, "args": ["-c", SERVER_CODE]}],
    }, ctx)
    try:
        await plugin.on_execution_start(None)
        first_count = len(plugin.get_tools())
        await plugin.on_execution_start(None)
    finally:
        await plugin.close()

    assert first_count == 3
    assert len(plugin.get_tools()) == 3


async def test_mcp_plugin_filters_and_sets_risk_metadata():
    ctx = PluginContext()
    ctx.tool_registry = ToolRegistry()
    plugin = MCPPlugin()
    plugin.initialize({
        "allowed_tools": ["mcp.local.echo", "mcp.local.write"],
        "tool_overrides": {
            "mcp.local.write": {"read_only": False, "risk_level": "dangerous", "capability": "mcp_write", "timeout": 7},
        },
        "servers": [{"name": "local", "command": sys.executable, "args": ["-c", SERVER_CODE]}],
    }, ctx)
    try:
        await plugin.on_execution_start(None)
        tools = {tool.name: tool for tool in plugin.get_tools()}
    finally:
        await plugin.close()

    assert set(tools) == {"mcp.local.echo", "mcp.local.write"}
    assert tools["mcp.local.echo"].is_read_only is True
    assert tools["mcp.local.echo"].risk_level == "safe"
    assert tools["mcp.local.write"].is_read_only is False
    assert tools["mcp.local.write"].risk_level == "dangerous"
    assert tools["mcp.local.write"].capability == "mcp_write"
    assert tools["mcp.local.write"].timeout == 7


async def test_mcp_plugin_accepts_short_tool_filter_names():
    ctx = PluginContext()
    ctx.tool_registry = ToolRegistry()
    plugin = MCPPlugin()
    plugin.initialize({
        "allowed_tools": ["echo"],
        "servers": [{"name": "local", "command": sys.executable, "args": ["-c", SERVER_CODE]}],
    }, ctx)
    try:
        await plugin.on_execution_start(None)
        tools = {tool.name for tool in plugin.get_tools()}
    finally:
        await plugin.close()

    assert tools == {"mcp.local.echo"}


async def test_mcp_connection_serializes_concurrent_stdio_requests():
    conn = MCPConnection({"name": "local", "command": sys.executable, "args": ["-c", SERVER_CODE]})
    try:
        await conn.connect()
        results = await asyncio.gather(
            conn.call_tool("echo", {"text": "a"}),
            conn.call_tool("echo", {"text": "b"}),
        )
    finally:
        await conn.close()

    assert results == ["echo:a", "echo:b"]


async def test_mcp_large_result_externalized():
    ctx = PluginContext()
    ctx.tool_registry = ToolRegistry()
    plugin = MCPPlugin()
    plugin.initialize({
        "max_result_bytes": 10,
        "servers": [{"name": "local", "command": sys.executable, "args": ["-c", SERVER_CODE]}],
    }, ctx)
    store = InMemoryArtifactStore()
    try:
        await plugin.on_execution_start(None)
        tool = next(tool for tool in plugin.get_tools() if tool.name == "mcp.local.large")
        result = await tool.execute({}, {"artifact_store": store})
    finally:
        await plugin.close()

    assert result.artifacts
    assert result.content["externalized"] is True
    assert (await store.read(result.artifacts[0].id, limit=3)).content == "xxx"


async def test_mcp_large_result_externalize_failure_returns_tool_error():
    class FailingStore:
        async def put_text(self, content, metadata=None, *, kind="text", run_id="", durable=False, expires_at=None):
            raise RuntimeError("store down")

    ctx = PluginContext()
    ctx.tool_registry = ToolRegistry()
    plugin = MCPPlugin()
    plugin.initialize({
        "max_result_bytes": 10,
        "servers": [{"name": "local", "command": sys.executable, "args": ["-c", SERVER_CODE]}],
    }, ctx)
    try:
        await plugin.on_execution_start(None)
        tool = next(tool for tool in plugin.get_tools() if tool.name == "mcp.local.large")
        result = await tool.execute({}, {"artifact_store": FailingStore()})
    finally:
        await plugin.close()

    assert result.is_error is True
    assert result.content == "store down"


async def test_mcp_duplicate_server_does_not_keep_partial_tools():
    ctx = PluginContext()
    ctx.tool_registry = ToolRegistry()
    plugin = MCPPlugin()
    plugin.initialize({
        "servers": [
            {"name": "dup", "command": sys.executable, "args": ["-c", SERVER_CODE]},
            {"name": "dup", "command": sys.executable, "args": ["-c", SERVER_CODE]},
        ],
    }, ctx)
    try:
        try:
            await plugin.on_execution_start(None)
        except RuntimeError:
            pass
        tools = plugin.get_tools()
    finally:
        await plugin.close()

    assert [tool.name for tool in tools] == ["mcp.dup.echo", "mcp.dup.write", "mcp.dup.large"]
