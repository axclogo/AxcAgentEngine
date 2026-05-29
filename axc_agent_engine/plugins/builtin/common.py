"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
内置插件公共 helper。

English: Shared helpers for builtin plugins. Keep this module small and only
place logic here when multiple plugins already use the same behavior."""
from __future__ import annotations

import logging
from typing import Any

from axc_agent_engine.core.events import Event, EventType


def bounded_int(value: Any, minimum: int, maximum: int) -> int:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把配置值限制在整数区间内。

	English: Parse and clamp a configuration value into an integer range.
	"""
	try:
		parsed = int(value)
	except (TypeError, ValueError):
		return minimum
	return max(minimum, min(maximum, parsed))


def exec_ctx_from_tool_context(context: dict) -> Any:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
从工具上下文字典中提取 ExecutionContext。

	English: Extract ExecutionContext from a tool context dictionary.
	"""
	return context.get("exec_ctx") if isinstance(context, dict) else None


def result_store_from_context(context: dict, plugin_ctx: Any = None) -> Any:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
优先从工具上下文获取 ResultStore，否则回退到 PluginContext。

	English: Resolve ResultStore from tool context first, then PluginContext.
	"""
	if isinstance(context, dict) and context.get("result_store"):
		return context["result_store"]
	return getattr(plugin_ctx, "result_store", None)


def agent_event_callback(exec_ctx: Any):
	"""English: Bridge dispatcher envelopes into run event sink. 中文：转发子 Agent 事件到当前运行流。"""
	event_sink = getattr(getattr(exec_ctx, "runtime", None), "event_sink", None) if exec_ctx else None
	if not event_sink:
		return None

	def callback(envelope: Any) -> None:
		event_type = EventType(envelope.type)
		event_sink(Event(type=event_type, content=envelope.content, metadata=envelope.metadata))

	return callback


async def externalize_text(
	content: Any,
	result_store: Any,
	threshold_bytes: int,
	metadata: dict[str, Any],
	logger: logging.Logger,
	source: str,
	preview_chars: int = 0,
) -> tuple[Any, Any]:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把超阈值文本外置到 ResultStore。

	English: Externalize oversized text into ResultStore and return (payload, artifact_ref).
	"""
	text = str(content)
	size = len(text.encode("utf-8"))
	if not result_store or size <= threshold_bytes:
		return content, None
	try:
		ref = await result_store.put(text, metadata=metadata)
	except Exception as e:
		logger.warning("[%s] failed to externalize large result: %s", source, e)
		return content, None
	payload = {"artifact_id": ref.id, "size": ref.size, "truncated": True}
	if preview_chars > 0:
		payload["preview"] = text[:preview_chars]
	return payload, ref
