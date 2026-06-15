"""Input message normalization helpers.
中文：输入消息归一化辅助函数。"""
from __future__ import annotations

from typing import Any

from axc_agent_engine.core.run_context import dict_or_empty


def extract_last_user_message(messages: list[dict]) -> str:
	for msg in reversed(messages):
		if msg.get("role") == "user":
			return content_to_text(msg.get("content", ""))
	return ""


def content_to_text(content: Any) -> str:
	"""Extract textual goal from string or OpenAI-compatible multimodal content.
中文：从字符串或 OpenAI 兼容多模态内容中提取文本目标。"""
	if isinstance(content, str):
		return content
	if isinstance(content, list):
		text_parts = []
		for part in content:
			if isinstance(part, dict) and part.get("type") == "text":
				text = part.get("text", "")
				if text:
					text_parts.append(str(text))
		return "\n".join(text_parts)
	return "" if content is None else str(content)


def normalize_multimodal_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
	return [{**message, "content": normalize_content_parts(message.get("content"))} for message in messages]


def normalize_content_parts(content: Any) -> Any:
	if not isinstance(content, list):
		return content
	parts = []
	for part in content:
		if not isinstance(part, dict):
			raise TypeError("message content part must be an object")
		part_type = str(part.get("type") or "")
		if part_type == "text":
			parts.append({"type": "text", "text": str(part.get("text", ""))})
		elif part_type == "image_url":
			image = part.get("image_url")
			if not isinstance(image, dict) or not image.get("url"):
				raise ValueError("image_url content part requires image_url.url")
			parts.append({"type": "image_url", "image_url": dict(image)})
		elif part_type == "image_base64":
			data = str(part.get("data") or part.get("image_base64") or "")
			media_type = str(part.get("media_type") or "image/png")
			if not data:
				raise ValueError("image_base64 content part requires data")
			parts.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}})
		elif part_type == "file_ref":
			ref = str(part.get("ref") or part.get("file_ref") or "")
			if not ref:
				raise ValueError("file_ref content part requires ref")
			metadata = dict_or_empty(part.get("metadata"), "file_ref.metadata")
			parts.append({"type": "file_ref", "file_ref": {"ref": ref, **metadata}})
		else:
			raise ValueError(f"unsupported message content part type: {part_type}")
	return parts
