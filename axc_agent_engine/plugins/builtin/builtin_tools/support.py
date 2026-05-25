"""Shared helpers for builtin tool implementations."""
from typing import Any


def bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
	try:
		number = int(value)
	except (TypeError, ValueError):
		return default
	return max(minimum, min(number, maximum))


def truncate_by_bytes(text: str, max_bytes: int) -> str:
	data = text.encode()
	if len(data) <= max_bytes:
		return text
	return data[:max_bytes].decode(errors="ignore")
