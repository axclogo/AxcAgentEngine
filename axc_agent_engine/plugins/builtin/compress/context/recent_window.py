"""L2 recent round window selection.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from typing import Any


def select_recent_window(messages: list[dict[str, Any]], rounds: int) -> list[dict[str, Any]]:
	"""Keep system messages and the most recent N user rounds with tool pairs.
中文：此文档说明相关引擎组件的行为。"""
	if rounds <= 0:
		return [m for m in messages if m.get("role") == "system"]
	max_round = max((int(m.get("round", 0)) for m in messages), default=0)
	cutoff = max(1, max_round - rounds + 1)
	keep = _initial_keep(messages, cutoff)
	keep |= _paired_tool_indexes(messages, keep)
	return [message for index, message in enumerate(messages) if index in keep]


def _initial_keep(messages: list[dict[str, Any]], cutoff: int) -> set[int]:
	keep: set[int] = set()
	for index, message in enumerate(messages):
		if message.get("role") == "system" or int(message.get("round", 0)) >= cutoff:
			keep.add(index)
	return keep


def _paired_tool_indexes(messages: list[dict[str, Any]], keep: set[int]) -> set[int]:
	call_to_assistant: dict[str, int] = {}
	result_to_tool: dict[str, int] = {}
	for index, message in enumerate(messages):
		for call in message.get("tool_calls", []) or []:
			call_id = call.get("id")
			if call_id:
				call_to_assistant[call_id] = index
		if message.get("role") == "tool" and message.get("tool_call_id"):
			result_to_tool[message["tool_call_id"]] = index
	for call_id, assistant_index in call_to_assistant.items():
		tool_index = result_to_tool.get(call_id)
		if tool_index in keep:
			keep.add(assistant_index)
		if assistant_index in keep and tool_index is not None:
			keep.add(tool_index)
	return keep
