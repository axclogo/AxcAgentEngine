import asyncio
import builtins
import os
import sys

import pytest

from axc_agent_engine import cli
from axc_agent_engine.core.events import Event, EventType


def test_main_prints_help_without_command(monkeypatch, capsys):
	monkeypatch.setattr(sys, "argv", ["axc"])
	cli.main()
	assert "AxcAgentEngine CLI" in capsys.readouterr().out


def test_chat_requires_env(monkeypatch):
	monkeypatch.delenv("AXC_LLM_BASE_URL", raising=False)
	monkeypatch.delenv("AXC_LLM_API_KEY", raising=False)
	with pytest.raises(SystemExit):
		asyncio.run(cli._chat("agent.yaml"))


def test_serve_requires_env(monkeypatch):
	monkeypatch.delenv("AXC_LLM_BASE_URL", raising=False)
	monkeypatch.delenv("AXC_LLM_API_KEY", raising=False)
	with pytest.raises(SystemExit):
		cli._serve("agent.yaml", 8000)


def test_serve_import_error(monkeypatch):
	monkeypatch.setitem(os.environ, "AXC_LLM_BASE_URL", "http://x")
	monkeypatch.setitem(os.environ, "AXC_LLM_API_KEY", "k")
	orig_import = builtins.__import__
	def fake_import(name, *args, **kwargs):
		if name == "uvicorn":
			raise ImportError("no uvicorn")
		return orig_import(name, *args, **kwargs)
	monkeypatch.setattr(builtins, "__import__", fake_import)
	with pytest.raises(SystemExit):
		cli._serve("agent.yaml", 8000)


async def test_chat_stream_loop(monkeypatch, capsys):
	class Agent:
		name = "demo"
		async def stream(self, message):
			yield Event.delta("hi")
			yield Event(type=EventType.STREAM_END, content="")
			yield Event.tool_call("tool", "1", {})
			yield Event.tool_result("tool", "1", "ok")
			yield Event.error("bad")
	class Engine:
		def __init__(self, *args, **kwargs):
			pass
		def load_agent(self, path):
			return Agent()
		async def close(self):
			self.closed = True

	monkeypatch.setenv("AXC_LLM_BASE_URL", "http://x")
	monkeypatch.setenv("AXC_LLM_API_KEY", "k")
	monkeypatch.setattr(cli, "Engine", Engine)
	inputs = iter(["hello", "/quit"])
	monkeypatch.setattr(builtins, "input", lambda prompt: next(inputs))
	await cli._chat("agent.yaml")
	out = capsys.readouterr().out
	assert "已加载 Agent: demo" in out
	assert "[工具调用: tool]" in out


def test_cli_plugin_registry_contains_builtins():
	registry = cli._cli_plugin_registry()
	assert registry.get("safety") is not None
