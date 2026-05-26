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
	budget = max(1, max_input_tokens - reserve_output_tokens)
	required = _required_indexes(messages)
	selected: set[int] = set()
	used = 0
	for index in required:
		used += _message_tokens(messages[index])
		selected.add(index)
	for index in reversed(range(len(messages))):
		if index in selected:
			continue
		cost = _message_tokens(messages[index])
		if used + cost <= budget:
			selected.add(index)
			used += cost
	truncated = len(selected) < len(messages)
	packed = [public_message(message) for index, message in enumerate(messages) if index in selected]
	if truncated:
		packed.insert(_placeholder_index(packed), {"role": "system", "content": "[上下文已截断]"})
	return PackedContext(messages=packed, estimated_tokens=used, truncated=truncated)


def _required_indexes(messages: list[dict[str, Any]]) -> set[int]:
	required = {i for i, m in enumerate(messages) if m.get("role") == "system"}
	for i in range(len(messages) - 1, -1, -1):
		if messages[i].get("role") == "user":
			required.add(i)
			break
	return required


def _message_tokens(message: dict[str, Any]) -> int:
	return int(message.get("token_estimate") or estimate_tokens(message.get("content", "")))


def _placeholder_index(messages: list[dict[str, Any]]) -> int:
	if messages and messages[0].get("role") == "system":
		return 1
	return 0
