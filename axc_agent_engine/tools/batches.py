"""Tool-call batch planning."""
from __future__ import annotations

from axc_agent_engine.tools.registry import ToolRegistry


def partition_tool_calls(tool_calls: list[dict], registry: ToolRegistry) -> list[dict]:
	"""把工具调用划分为批次：连续只读调用并发，其他调用串行。"""
	batches: list[dict] = []
	for tc in tool_calls:
		name = tc.get("name", "")
		tool_def = registry.get(name)
		is_read_only = tool_def.is_read_only if tool_def else False
		if is_read_only:
			if batches and batches[-1]["concurrent"]:
				batches[-1]["calls"].append(tc)
			else:
				batches.append({"concurrent": True, "calls": [tc]})
		else:
			batches.append({"concurrent": False, "calls": [tc]})
	return batches
