"""MCP SDK/JSON-RPC payload normalization. 规范化 MCP SDK 和 JSON-RPC 数据。"""

from typing import Any


def normalize_call_result(result: dict[str, Any]) -> Any:
	content = result.get("content", [])
	if isinstance(content, list):
		texts = [item.get("text", "") for item in content if item.get("type") == "text"]
		if texts:
			return "\n".join(texts)
		if content:
			return content
	return result
