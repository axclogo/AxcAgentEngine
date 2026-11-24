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
	assert "AxcAgentEngine 命令行工具" in capsys.readouterr().out


def test_main_dispatches_chat_and_serve(monkeypatch):
	calls = []

	async def fake_chat(path):
		calls.append(("chat", path))

	def fake_run(coro):
		loop = asyncio.new_event_loop()
		try:
			return loop.run_until_complete(coro)
		finally:
			loop.close()

	def fake_serve(agent, port, agents_dir="", *, host):
		calls.append(("serve", agent, port, agents_dir, host))

	monkeypatch.setattr(sys, "argv", ["axc", "chat", "--agent", "a.yaml"])
	monkeypatch.setattr(cli.asyncio, "run", fake_run)
	monkeypatch.setattr(cli, "_chat", fake_chat)
	cli.main()
	monkeypatch.setattr(
		sys,
		"argv",
		["axc", "serve", "--agent", "b.yaml", "--port", "9999", "--agents-dir", "agents", "--host", "127.0.0.1"],
	)
	monkeypatch.setattr(cli, "_serve", fake_serve)
	cli.main()

	assert calls == [("chat", "a.yaml"), ("serve", "b.yaml", 9999, "agents", "127.0.0.1")]


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

		def load_agent_template(self, path):
			class Template:
				def instantiate(self, *, models, mounts=None, metadata=None, overrides=None):
					return Agent()

			return Template()

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


async def test_chat_ignores_empty_input_done_empty_and_handles_eof(monkeypatch, capsys):
	class Agent:
		name = "demo"

		async def stream(self, message):
			yield Event.done("")

	class Engine:
		def load_agent_template(self, path):
			class Template:
				def instantiate(self, *, models, mounts=None, metadata=None, overrides=None):
					return Agent()

			return Template()

		async def close(self):
			pass

	monkeypatch.setenv("AXC_LLM_BASE_URL", "http://x")
	monkeypatch.setenv("AXC_LLM_API_KEY", "k")
	monkeypatch.setattr(cli, "Engine", lambda *args, **kwargs: Engine())
	inputs = iter(["", "hello"])

	def fake_input(prompt):
		try:
			return next(inputs)
		except StopIteration:
			raise EOFError()

	monkeypatch.setattr(builtins, "input", fake_input)

	await cli._chat("agent.yaml")

	assert "再见" in capsys.readouterr().out


def test_serve_success_invokes_uvicorn(monkeypatch, tmp_path, capsys):
	calls = {}
	agent_path = tmp_path / "agent.yaml"
	agent_path.write_text("name: a", encoding="utf-8")

	class Template:
		def instantiate(self, *, models, mounts=None, metadata=None, overrides=None):
			calls["instantiated"] = True

	class Engine:
		def __init__(self, *args, **kwargs):
			pass

		def load_agent_template(self, path):
			calls["path"] = path
			return Template()

	def fake_create_app(engine, models=None, agents_dir=""):
		calls["agents_dir"] = agents_dir
		return "app"

	class Uvicorn:
		@staticmethod
		def run(app, host, port, log_level):
			calls["uvicorn"] = (app, host, port, log_level)

	monkeypatch.setenv("AXC_LLM_BASE_URL", "http://x")
	monkeypatch.setenv("AXC_LLM_API_KEY", "k")
	monkeypatch.setitem(sys.modules, "uvicorn", Uvicorn)
	monkeypatch.setitem(sys.modules, "axc_agent_engine.api.app", type("AppMod", (), {"create_app": fake_create_app}))
	monkeypatch.setattr(cli, "Engine", Engine)

	cli._serve(str(agent_path), 1234, host="127.0.0.1")

	assert calls["instantiated"] is True
	assert calls["agents_dir"] == str(tmp_path)
	assert calls["uvicorn"] == ("app", "127.0.0.1", 1234, "info")
	assert "http://127.0.0.1:1234" in capsys.readouterr().out


def test_provider_from_config_wraps_rate_limited_when_configured(monkeypatch):
	class Client:
		def __init__(self, config):
			self.config = config

	class Limited:
		def __init__(self, provider, **kwargs):
			self.provider = provider
			self.kwargs = kwargs

	monkeypatch.setattr(cli, "OpenAIClient", Client)
	monkeypatch.setattr(cli, "RateLimitedProvider", Limited)
	config = cli.LLMConfig(base_url="u", api_key="k", model="m", max_concurrent_requests=2)

	provider = cli._provider_from_config(config)

	assert isinstance(provider, Limited)
	assert provider.kwargs["max_concurrent"] == 2


def test_cli_plugin_registry_contains_builtins():
	registry = cli._cli_plugin_registry()
	assert registry.get("safety") is not None


@pytest.mark.parametrize(
	"settings,reason,driver",
	[
		(
			"runtime: {strategy: por}\nplugins: {por: {enabled: true, mode: auto}, builtin_tools: {enabled: true, load: [get_time]}}",
			"explicit",
			"por",
		),
		("plugins: {react: {enabled: true}}", "single_enabled", "react"),
		("runtime: {routing: {mode: react_only}}", "runtime_routing", "react"),
		("", "runtime_routing", "por"),
	],
)
def test_inspect_reports_resolved_configuration_without_credentials(
	tmp_path, monkeypatch, capsys, settings, reason, driver
):
	import json

	path = tmp_path / "inspect.yaml"
	path.write_text("name: inspected\n" + settings)
	monkeypatch.delenv("AXC_LLM_API_KEY", raising=False)
	monkeypatch.delenv("AXC_LLM_BASE_URL", raising=False)
	monkeypatch.setattr(sys, "argv", ["axc", "inspect", "--agent", str(path)])
	cli.main()
	result = json.loads(capsys.readouterr().out)
	assert result["strategy"]["name"] == driver
	assert result["strategy"]["reason"] == reason
	assert result["policy"] == "CapabilityPolicyEvaluator"
	if reason == "explicit":
		assert result["strategy"]["settings"] == {"mode": "auto", "max_parallel_steps": 4}
		assert [tool["name"] for tool in result["tools"]] == ["get_time"]
		assert result["plugins"] == ["builtin_tools", "por"]
