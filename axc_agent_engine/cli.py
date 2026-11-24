"""CLI 入口。

English: Command-line entry point for local chat and API serving.
"""

import argparse
import asyncio
import json
import os
import sys

from axc_agent_engine.core.events import EventType
from axc_agent_engine.engine import AgentModels, Engine
from axc_agent_engine.llm.client import OpenAIClient
from axc_agent_engine.llm.config import LLMConfig
from axc_agent_engine.llm.rate_limited import RateLimitedProvider
from axc_agent_engine.observability.logging import setup_logging
from axc_agent_engine.plugins.builtin.registry import AVAILABLE_BUILTIN_PLUGINS
from axc_agent_engine.plugins.registry import PluginRegistry


def main() -> None:
	parser = argparse.ArgumentParser(prog="axc", description="AxcAgentEngine 命令行工具")
	parser.add_argument("--log-level", default="INFO", help="日志等级")
	parser.add_argument("--json-logs", action="store_true", help="输出 JSON 格式日志")
	sub = parser.add_subparsers(dest="command")
	# chat 子命令
	chat_parser = sub.add_parser("chat", help="交互式对话")
	chat_parser.add_argument("--agent", required=True, help="Agent YAML 路径")
	inspect_parser = sub.add_parser("inspect", help="显示有效执行策略、插件顺序、工具和权限")
	inspect_parser.add_argument("--agent", required=True, help="Agent YAML 路径")
	serve_parser = sub.add_parser("serve", help="启动 REST API 服务")
	serve_parser.add_argument("--agent", required=True, help="Agent YAML 路径")
	serve_parser.add_argument("--agents-dir", default="", help="Agent YAML 目录（可选）")
	serve_parser.add_argument("--host", default="0.0.0.0")
	serve_parser.add_argument("--port", type=int, default=8000)
	args = parser.parse_args()
	setup_logging(args.log_level, args.json_logs)
	if args.command == "chat":
		asyncio.run(_chat(args.agent))
	elif args.command == "inspect":
		asyncio.run(_inspect(args.agent))
	elif args.command == "serve":
		_serve(args.agent, args.port, args.agents_dir, host=args.host)
	else:
		parser.print_help()


async def _chat(agent_path: str) -> None:
	"""Run an interactive terminal chat loop.
	交互式对话循环。"""
	provider = _provider_from_env()
	engine = Engine(plugin_registry=_cli_plugin_registry())
	template = engine.load_agent_template(agent_path)
	agent = template.instantiate(models=AgentModels(default=provider))
	print(f"已加载 Agent: {agent.name}")
	print("输入 /quit 退出\n")
	try:
		while True:
			user_input = input("You: ").strip()
			if not user_input:
				continue
			if user_input in ("/quit", "/exit", "/q"):
				break
			print("Assistant: ", end="", flush=True)
			async for event in agent.stream(user_input):
				if event.type == EventType.STREAM_DELTA:
					print(event.content, end="", flush=True)
				elif event.type == EventType.STREAM_END:
					print(event.content)
				elif event.type == EventType.TOOL_CALL:
					print(f"\n  [工具调用: {event.tool_name}]", flush=True)
				elif event.type == EventType.TOOL_RESULT:
					print(f"  [结果: {event.content}]", flush=True)
				elif event.type == EventType.DONE:
					if not event.content:
						print()
				elif event.type == EventType.ERROR:
					print(f"\n  [错误: {event.content}]")
			print()
	except (KeyboardInterrupt, EOFError):
		print("\n再见!")
	finally:
		await engine.close()


def _serve(agent_path: str, port: int, agents_dir: str = "", *, host: str = "0.0.0.0") -> None:
	"""Start the REST API server.
	启动 REST API 服务。"""
	try:
		import uvicorn

		from axc_agent_engine.api.app import create_app
	except ImportError:
		print("需要安装 API 依赖：pip install axc-agent-engine[api]")
		sys.exit(1)
	provider = _provider_from_env()
	engine = Engine(plugin_registry=_cli_plugin_registry())
	# Preload the configured agent. 中文：预加载指定的 Agent
	models = AgentModels(default=provider)
	engine.load_agent_template(agent_path).instantiate(models=models)
	app = create_app(engine, models=models, agents_dir=agents_dir or os.path.dirname(os.path.abspath(agent_path)))
	print(f"API 服务启动: http://{host}:{port}")
	print(f"Agent: {agent_path}")
	uvicorn.run(app, host=host, port=port, log_level="info")


async def _inspect(agent_path: str) -> None:
	engine = Engine(plugin_registry=_cli_plugin_registry())
	provider = _provider_from_env(require_credentials=False)
	try:
		agent = engine.load_agent_template(agent_path).instantiate(models=AgentModels(default=provider))
		print(json.dumps(agent.inspect(), ensure_ascii=False, indent=2))
	finally:
		await engine.close()
		await provider.close()


def _provider_from_env(require_credentials: bool = True):
	llm_config = LLMConfig(
		base_url=os.environ.get("AXC_LLM_BASE_URL", ""),
		api_key=os.environ.get("AXC_LLM_API_KEY", ""),
		model=os.environ.get("AXC_LLM_MODEL", "gpt-4o"),
	)
	if require_credentials and not (llm_config.base_url and llm_config.api_key):
		print("错误: 请设置 AXC_LLM_BASE_URL 和 AXC_LLM_API_KEY 环境变量")
		sys.exit(1)
	return _provider_from_config(llm_config)


def _cli_plugin_registry() -> PluginRegistry:
	"""CLI 宿主显式允许全部内置插件，Agent YAML 再选择启用项。
	CLI host explicitly allows all builtin plugins; Agent YAML then chooses enabled ones.
	"""
	registry = PluginRegistry()
	registry.register_many(AVAILABLE_BUILTIN_PLUGINS.values())
	return registry


def _provider_from_config(config: LLMConfig):
	provider = OpenAIClient(config)
	if config.max_concurrent_requests > 0 or config.requests_per_minute > 0:
		return RateLimitedProvider(
			provider,
			max_concurrent=config.max_concurrent_requests,
			requests_per_minute=config.requests_per_minute,
			queue_timeout=config.rate_limit_queue_timeout,
		)
	return provider


if __name__ == "__main__":
	main()
