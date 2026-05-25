"""JSON 提取工具 — 从 LLM 文本响应中提取 JSON。"""
import json
import re
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
	"""从文本中提取第一个 JSON 对象，失败返回空 dict"""
	text = text.strip()
	for candidate in _json_candidates(text, "{", "}"):
		try:
			result = json.loads(candidate)
		except json.JSONDecodeError:
			continue
		if isinstance(result, dict):
			return result
	return {}


def extract_json_array(text: str) -> list[dict] | None:
	"""从文本中提取 JSON 数组，失败返回 None"""
	text = text.strip()
	for candidate in _json_candidates(text, "[", "]"):
		try:
			data = json.loads(candidate)
		except json.JSONDecodeError:
			continue
		if isinstance(data, list):
			return data
	return None


def _json_candidates(text: str, open_char: str, close_char: str) -> list[str]:
	"""按可信度返回可能的 JSON 片段。"""
	if not text:
		return []
	candidates = [text]
	fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
	if fence:
		candidates.append(fence.group(1))
	balanced = _extract_balanced(text, open_char, close_char)
	if balanced:
		candidates.append(balanced)
	return candidates


def _extract_balanced(text: str, open_char: str, close_char: str) -> str:
	start = text.find(open_char)
	if start < 0:
		return ""
	depth = 0
	in_string = False
	escape = False
	for idx in range(start, len(text)):
		char = text[idx]
		if escape:
			escape = False
			continue
		if char == "\\":
			escape = True
			continue
		if char == '"':
			in_string = not in_string
			continue
		if in_string:
			continue
		if char == open_char:
			depth += 1
		elif char == close_char:
			depth -= 1
			if depth == 0:
				return text[start:idx + 1]
	return ""
