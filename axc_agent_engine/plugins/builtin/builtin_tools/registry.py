"""Tool registry for builtin tools.
中文：此文档说明相关引擎组件的行为。"""
from collections.abc import Callable
from typing import Any

from axc_agent_engine.core.schema import ToolDefinition


DEFAULT_TOOLS = ["get_time"]
ALL_TOOLS: dict[str, ToolDefinition] = {}


def register_tool(
	name: str,
	description: str,
	parameters: dict,
	is_read_only: bool = False,
	capability: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
	"""Declare a builtin tool and store its OpenAI schema metadata.
中文：此文档说明相关引擎组件的行为。"""
	def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
		ALL_TOOLS[name] = ToolDefinition(
			name=name,
			description=description,
			parameters=parameters,
			execute=fn,
			is_read_only=is_read_only,
			capability=capability,
		)
		return fn
	return decorator
