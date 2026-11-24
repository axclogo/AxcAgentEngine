"""Shared helpers for builtin plugins. Keep this module small and only
place logic here when multiple plugins already use the same behavior.
内置插件公共 helper。"""

from __future__ import annotations

import logging
import math
from typing import Any

from axc_agent_engine.core.errors import ConfigValueError
from axc_agent_engine.core.events import Event, EventType


def string_array(value: Any, field: str) -> list[str]:
	"""Validate a string array at a resource boundary. 校验外部资源的字符串数组。"""
	if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
		raise TypeError(f"{field} must be an array of strings")
	return value


def bounded_int(value: Any, minimum: int, maximum: int) -> int:
	"""Clamp an integer tool argument to its supported range. 将整数工具参数限制在支持范围内。"""
	if type(value) is not int:
		raise ConfigValueError("Expected an integer")
	return max(minimum, min(maximum, value))


def strict_bounded_int(value: Any, minimum: int, maximum: int, field_name: str) -> int:
	"""Parse config integer and fail on invalid range.
	中文：解析配置整数，类型或范围错误直接失败。"""
	if type(value) is not int:
		raise ConfigValueError(f"{field_name} must be an integer")
	if not minimum <= value <= maximum:
		raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
	return value


def exec_ctx_from_tool_context(context: dict) -> Any:
	"""Extract ExecutionContext from a tool context dictionary.
	从工具上下文字典中提取 ExecutionContext。"""
	return context.get("exec_ctx")


def artifact_store_from_context(context: dict, plugin_ctx: Any = None) -> Any:
	"""Resolve ArtifactStore from tool context first, then PluginContext.
	优先从工具上下文获取 ArtifactStore，否则回退到 PluginContext。"""
	if (store := context.get("artifact_store")) is not None:
		return store
	return plugin_ctx.artifact_store if plugin_ctx is not None else None


def agent_event_callback(exec_ctx: Any):
	"""English: Bridge dispatcher envelopes into run event sink. 中文：转发子 Agent 事件到当前运行流。"""
	event_sink = getattr(getattr(exec_ctx, "runtime", None), "event_sink", None) if exec_ctx else None
	if not event_sink:
		return None

	def callback(envelope: Any) -> None:
		event_type = EventType(envelope.type)
		event_sink(Event(type=event_type, content=envelope.content, metadata=envelope.metadata))

	return callback


async def command_log_artifact(path: str, store: Any, metadata: dict):
	"""Register a complete on-disk stream. 为完整的磁盘输出创建制品引用。"""
	if store is None:
		raise RuntimeError(f"Large command output requires an ArtifactStore. Full log: {path}")
	ref = await store.put_file_ref(path, metadata, kind="file")
	return {
		"artifact_id": ref.id,
		"size": ref.size,
		"externalized": True,
		"read_with": ["artifact_read", "artifact_page"],
	}, ref


async def externalize_text(
	content: Any,
	artifact_store: Any,
	threshold_bytes: int,
	metadata: dict[str, Any],
	logger: logging.Logger,
	source: str,
	preview_chars: int = 0,
) -> tuple[Any, Any]:
	"""Externalize oversized text into ArtifactStore and return (payload, artifact_ref).
	把超阈值文本外置到 ArtifactStore。"""
	text = str(content)
	size = len(text.encode("utf-8"))
	if not artifact_store or size <= threshold_bytes:
		return content, None
	ref = await artifact_store.put_text(text, metadata=metadata, kind=str(metadata.get("kind") or "text"))
	return {"artifact_id": ref.id, "size": ref.size, "externalized": True}, ref


def bounded_timeout(value: Any) -> float:
	if type(value) not in (float, int) or (isinstance(value, float) and not math.isfinite(value)):
		raise ConfigValueError("timeout must be a finite number")
	return max(1.0, min(value, 3600.0))
