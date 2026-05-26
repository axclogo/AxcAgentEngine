"""L1 tool result management.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from typing import Any

from axc_agent_engine.plugins.builtin.compress.context.normalizer import estimate_tokens
from axc_agent_engine.tools.tool_output import ToolOutput


TOOL_COMPACT_MARKER = "[工具结果已压缩]"


def compact_tool_messages(messages: list[dict[str, Any]], max_inline_tokens: int) -> list[dict[str, Any]]:
	"""Compact oversized tool messages already stored in conversation history.
中文：此文档说明相关引擎组件的行为。"""
	result: list[dict[str, Any]] = []
	for message in messages:
		if message.get("role") != "tool":
			result.append(message)
			continue
		content = str(message.get("content", ""))
		if estimate_tokens(content) <= max_inline_tokens:
			result.append(message)
		else:
			result.append({**message, "content": _compact_text(content, max_inline_tokens)})
	return result


async def externalize_large_tool_output(
	output: ToolOutput,
	result_store: Any,
	artifact_threshold_tokens: int,
) -> ToolOutput:
	"""Move large tool output content to ResultStore when available.
中文：此文档说明相关引擎组件的行为。"""
	if output.is_error or result_store is None:
		return output
	content = output._content_as_str()
	if estimate_tokens(content) <= artifact_threshold_tokens:
		return output
	artifact = await result_store.put(content, {"kind": output.content_type, **output.metadata})
	summary = output.summary or _compact_text(content, 300)
	return ToolOutput(
		content=summary,
		content_type=output.content_type,
		summary=summary,
		artifacts=[*output.artifacts, artifact],
		metadata=output.metadata,
	)


def _compact_text(content: str, max_tokens: int) -> str:
	max_chars = max(80, max_tokens * 4)
	head_len = max_chars * 3 // 4
	tail_len = max_chars - head_len
	head = content[:head_len]
	tail = content[-tail_len:] if tail_len else ""
	omitted = max(0, len(content) - len(head) - len(tail))
	return f"{TOOL_COMPACT_MARKER}\n摘要：{head}\n...[省略 {omitted} 个字符]...\n{tail}"
