"""工具模块公共工具函数"""
import json
from typing import Any


def parse_tool_calls(tool_calls: list) -> list[dict[str, Any]]:
	"""把 LLM 响应里的原始 tool_calls 解析成统一格式。

	参数：
		tool_calls：LLM 响应中的原始 tool_calls 列表。

	返回：
		解析后的工具调用 dict 列表，包含 id、name、arguments。
	"""
	parsed = []
	for tc in tool_calls:
		fn = tc.get("function", {})
		parsed.append({
			"id": tc.get("id", ""),
			"name": fn.get("name", ""),
			"arguments": parse_arguments(fn.get("arguments", "{}")),
		})
	return parsed


def parse_arguments(raw: str) -> dict[str, Any]:
	"""从 JSON 字符串解析工具调用参数。

	参数：
		raw：参数 JSON 字符串。

	返回：
		解析后的 dict；解析失败时返回 {"_raw": raw}。
	"""
	if not raw:
		return {}
	try:
		return json.loads(raw)
	except json.JSONDecodeError:
		return {"_raw": raw}
