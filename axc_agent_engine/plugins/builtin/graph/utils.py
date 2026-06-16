"""Small pure helpers for graph plugin data shaping.
中文：此文档说明相关引擎组件的行为。"""
from typing import Any

from axc_agent_engine.core.schema import ToolDefinition


def metadata(value: Any, namespace: str) -> dict[str, Any]:
	import time
	data = dict(value) if isinstance(value, dict) else {}
	data.setdefault("namespace", namespace)
	data.setdefault("updated_at", time.time())
	return data


def graph_tool(
	name: str,
	description: str,
	properties: dict,
	required: list[str],
	is_read_only: bool,
	capability: str,
	risk_level: str,
	execute: Any,
) -> ToolDefinition:
	return ToolDefinition(
		name=name,
		description=description,
		parameters={
			"type": "object",
			"properties": properties,
			**({"required": required} if required else {}),
		},
		is_read_only=is_read_only,
		capability=capability,
		risk_level=risk_level,
		execute=execute,
	)


def filter_metadata(value: dict[str, Any], include_metadata: bool) -> dict[str, Any]:
	if include_metadata:
		return dict(value)
	item = dict(value)
	item.pop("metadata", None)
	return item


def clean_text(value: Any, max_length: int) -> str:
	text = str(value or "").strip()
	if max_length <= 0:
		return ""
	return text
