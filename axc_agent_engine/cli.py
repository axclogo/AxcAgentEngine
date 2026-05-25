"""CLI 入口。

English: Command-line entry point for local chat and API serving.
"""
import argparse
import asyncio
import os
import sys

from axc_agent_engine.llm.config import LLMConfig
from axc_agent_engine.engine import Engine
from axc_agent_engine.core.events import EventType
from axc_agent_engine.observability.logging import setup_logging
from axc_agent_engine.plugins.builtin import AVAILABLE_BUILTIN_PLUGINS
from axc_agent_engine.plugins.registry import PluginRegistry


def main() -> None:
	parser = argparse.ArgumentParser(prog="axc", description="AxcAgentEngine CLI")
	parser.add_argument("--log-level", default="INFO", help="日志等级")
	parser.add_argument("--json-logs", action="store_true", help="输出 JSON 格式日志")
	sub = parser.add_subparsers(dest="command")
	# chat 子命令
	chat_parser = sub.add_parser("chat", help="交互式对话")
	chat_parser.add_argument("--agent", required=True, help="Agent YAML 路径")
	# serve 子命令（预留）
	serve_parser = sub.add_parser("serve", help="启动 REST API 服务")
	serve_parser.add_argument("--agent", required=True, help="Agent YAML 路径")
	serve_parser.add_argument("--agents-dir", default="", help="Agent YAML 目录（可选）")
	serve_parser.add_argument("--host", default="0.0.0.0")
	serve_parser.add_argument("--port", type=int, default=8000)
	args = parser.parse_args()
	setup_logging(args.log_level, args.json_logs)
	if args.command == "chat":
		asyncio.run(_chat(args.agent))
	elif args.command == "serve":
		_serve(args.agent, args.port, getattr(args, "agents_dir", ""))
	else:
		parser.print_help()


async def _chat(agent_path: str) -> None:
	"""交互式对话循环。

	English: Run an interactive terminal chat loop.
	"""
	llm_config = LLMConfig(
		base_url=os.environ.get("AXC_LLM_BASE_URL", ""),
		api_key=os.environ.get("AXC_LLM_API_KEY", ""),
		model=os.environ.get("AXC_LLM_MODEL", "gpt-4o"),
	)
	if not llm_config.base_url or not llm_config.api_key:
		print("错误: 请设置 AXC_LLM_BASE_URL 和 AXC_LLM_API_KEY 环境变量")
		sys.exit(1)
	engine = Engine(default_llm=llm_config, plugin_registry=_cli_plugin_registry())
	agent = engine.load_agent(agent_path)
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


def _serve(agent_path: str, port: int, agents_dir: str = "") -> None:
	"""启动 REST API 服务。

	English: Start the REST API server.
	"""
	try:
		import uvicorn
		from axc_agent_engine.api.app import create_app
	except ImportError:
		print("需要安装 API 依赖：pip install axc-agent-engine[api]")
		sys.exit(1)
	llm_config = LLMConfig(
		base_url=os.environ.get("AXC_LLM_BASE_URL", ""),
		api_key=os.environ.get("AXC_LLM_API_KEY", ""),
		model=os.environ.get("AXC_LLM_MODEL", "gpt-4o"),
	)
	if not llm_config.base_url or not llm_config.api_key:
		print("错误: 请设置 AXC_LLM_BASE_URL 和 AXC_LLM_API_KEY 环境变量")
		sys.exit(1)
	engine = Engine(default_llm=llm_config, plugin_registry=_cli_plugin_registry())
	# 预加载指定的 Agent
	engine.load_agent(agent_path)
	app = create_app(engine, agents_dir=agents_dir or os.path.dirname(agent_path))
	print(f"API 服务启动: http://0.0.0.0:{port}")
	print(f"Agent: {agent_path}")
	uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


def _cli_plugin_registry() -> PluginRegistry:
	"""CLI 宿主显式允许全部内置插件，Agent YAML 再选择启用项。
	CLI host explicitly allows all builtin plugins; Agent YAML then chooses enabled ones.
	"""
	registry = PluginRegistry()
	registry.register_many(AVAILABLE_BUILTIN_PLUGINS.values())
	return registry


if __name__ == "__main__":
	main()
