"""Shared helpers for builtin tool implementations.
中文：此文档说明相关引擎组件的行为。"""
from typing import Any


def bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
	try:
		number = int(value)
	except (TypeError, ValueError):
		return default
	return max(minimum, min(number, maximum))
