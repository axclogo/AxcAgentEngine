"""L0 message normalization."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


PUBLIC_MESSAGE_KEYS = {
	"role",
	"content",
	"name",
	"tool_call_id",
	"tool_calls",
	"function_call",
}


def normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Clean invalid messages and add lightweight context metadata."""
	normalized: list[dict[str, Any]] = []
	round_no = 0
	for index, raw in enumerate(messages):
		msg = _normalize_one(raw, index, round_no)
		if msg is None:
			continue
		if msg["role"] == "user":
			round_no += 1
			msg["round"] = round_no
		else:
			msg["round"] = round_no
		normalized.append(msg)
	return normalized


def public_message(message: dict[str, Any]) -> dict[str, Any]:
	"""Strip internal context metadata before sending messages to providers."""
	return {k: deepcopy(v) for k, v in message.items() if k in PUBLIC_MESSAGE_KEYS}


def estimate_tokens(value: Any) -> int:
	if value is None:
		return 0
	if not isinstance(value, str):
		value = json.dumps(value, ensure_ascii=False, default=str)
	return max(1, len(value) // 4)


def _normalize_one(raw: dict[str, Any], index: int, round_no: int) -> dict[str, Any] | None:
	if not isinstance(raw, dict):
		return None
	role = _role(raw.get("role"))
	if role == "tool" and not raw.get("tool_call_id"):
		return None
	if _empty(raw) and not raw.get("tool_calls"):
		return None
	msg = deepcopy(raw)
	msg["role"] = role
	msg.setdefault("content", "")
	msg.setdefault("created_at", index)
	msg.setdefault("round", round_no)
	msg.setdefault("token_estimate", estimate_tokens(msg.get("content", "")))
	if role == "tool":
		msg.setdefault("tool_name", raw.get("name", ""))
	return msg


def _role(value: object) -> str:
	return str(value or "user") if value in {"system", "user", "assistant", "tool"} else "user"


def _empty(message: dict[str, Any]) -> bool:
	content = message.get("content")
	if isinstance(content, str):
		return not content.strip()
	return content in (None, [], {})
