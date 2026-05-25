"""MCP SDK/JSON-RPC payload normalization."""
from typing import Any

from .models import MCPTool


def tool_from_payload(tool: dict[str, Any]) -> MCPTool:
	return MCPTool(
		name=tool.get("name", ""),
		description=tool.get("description", ""),
		input_schema=tool.get("inputSchema") or {"type": "object", "properties": {}},
		annotations=tool.get("annotations") or {},
	)


def normalize_call_result(result: dict[str, Any]) -> Any:
	content = result.get("content", [])
	if isinstance(content, list):
		texts = [item.get("text", "") for item in content if item.get("type") == "text"]
		if texts:
			return "\n".join(texts)
		if content:
			return content
	return result


def sdk_tool_to_dict(tool: Any) -> dict[str, Any]:
	return {
		"name": getattr(tool, "name", ""),
		"description": getattr(tool, "description", "") or "",
		"inputSchema": getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {"type": "object", "properties": {}},
		"annotations": getattr(tool, "annotations", None) or {},
	}


def sdk_call_result_to_dict(result: Any) -> dict[str, Any]:
	content = getattr(result, "content", [])
	items = []
	for item in content or []:
		if isinstance(item, dict):
			items.append(item)
			continue
		items.append({
			"type": getattr(item, "type", "text"),
			"text": getattr(item, "text", ""),
		})
	return {"content": items}
