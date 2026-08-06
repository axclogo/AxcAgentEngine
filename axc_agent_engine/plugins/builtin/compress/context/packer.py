"""L5 context window packing.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from typing import Any

from axc_agent_engine.plugins.builtin.compress.context.models import PackedContext
from axc_agent_engine.plugins.builtin.compress.context.normalizer import estimate_tokens, public_message


def pack_context(
	messages: list[dict[str, Any]],
	max_input_tokens: int,
	reserve_output_tokens: int,
) -> PackedContext:
	"""Pack messages into the configured input window.
中文：此文档说明相关引擎组件的行为。"""
	try:
		max_input = int(max_input_tokens)
		reserve_output = int(reserve_output_tokens)
	except (TypeError, ValueError) as exc:
		raise TypeError("context window token budgets must be integers") from exc
	if max_input < 0 or reserve_output < 0:
		raise ValueError("context window token budgets must be non-negative")

	budget = max(0, max_input - reserve_output)
	groups = _message_groups(messages)
	required_groups = _required_groups(messages, groups)
	selected_groups = set(required_groups)
	used = sum(_group_tokens(messages, groups[index]) for index in required_groups)

	# English: Prefer the newest optional context; 中文：优先保留最新的可选上下文。
	# English: Required groups may exceed budget to preserve semantics; 中文：必需分组可超预算保留，以维持请求语义。
	for group_index in range(len(groups) - 1, -1, -1):
		if group_index in selected_groups:
			continue
		group_tokens = _group_tokens(messages, groups[group_index])
		if used + group_tokens <= budget:
			selected_groups.add(group_index)
			used += group_tokens

	selected_indices = sorted({index for group_index in selected_groups for index in groups[group_index]})
	packed = [public_message(messages[index]) for index in selected_indices]
	return PackedContext(messages=packed, estimated_tokens=used)


def _message_groups(messages: list[dict[str, Any]]) -> list[list[int]]:
	"""Group assistant tool calls with their tool results.
中文：将 assistant 工具调用与对应 tool 结果分组。"""
	grouped = _tool_call_groups(messages)
	groups: list[list[int]] = []
	emitted: set[int] = set()
	for index in range(len(messages)):
		if index in emitted:
			continue
		group = grouped.get(index, [index])
		groups.append(group)
		emitted.update(group)
	return groups


def _tool_call_groups(messages: list[dict[str, Any]]) -> dict[int, list[int]]:
	call_to_assistant: dict[str, int] = {}
	for index, message in enumerate(messages):
		if message.get("role") != "assistant":
			continue
		for call in message.get("tool_calls", []) or []:
			call_id = call.get("id")
			if call_id:
				call_to_assistant[call_id] = index
	grouped: dict[int, set[int]] = {}
	for index, message in enumerate(messages):
		if message.get("role") != "tool":
			continue
		assistant_index = call_to_assistant.get(message.get("tool_call_id"))
		if assistant_index is not None:
			grouped.setdefault(assistant_index, {assistant_index}).add(index)
	return {index: sorted(group) for index, group in grouped.items()}


def _required_groups(messages: list[dict[str, Any]], groups: list[list[int]]) -> set[int]:
	required = {i for i, group in enumerate(groups) if any(messages[index].get("role") == "system" for index in group)}
	for group_index in range(len(groups) - 1, -1, -1):
		if any(messages[index].get("role") == "user" for index in groups[group_index]):
			required.add(group_index)
			break
	for group_index, group in enumerate(groups):
		if any(_pinned_message(messages[index]) for index in group):
			required.add(group_index)
	return required


def _pinned_message(message: dict[str, Any]) -> bool:
	metadata = message.get("metadata", {})
	return isinstance(metadata, dict) and bool(metadata.get("durable") or metadata.get("durable_summary"))


def _message_tokens(message: dict[str, Any]) -> int:
	return int(message.get("token_estimate") or estimate_tokens(message.get("content", "")))


def _group_tokens(messages: list[dict[str, Any]], group: list[int]) -> int:
	return sum(_message_tokens(messages[index]) for index in group)
