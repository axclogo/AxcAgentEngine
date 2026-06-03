"""Normalized per-run request objects.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunOptions:
	stream_idle_timeout: int | None = None
	approval_queue: Any = None
	response_queue: Any = None
	stream: bool | None = None
	run_id: str = ""

	@classmethod
	def from_dict(cls, raw: dict | None) -> "RunOptions":
		raw = raw or {}
		return cls(
			stream_idle_timeout=_positive_int(raw.get("stream_idle_timeout")),
			approval_queue=raw.get("approval_queue"),
			response_queue=raw.get("response_queue"),
			stream=raw.get("stream") if isinstance(raw.get("stream"), bool) else None,
			run_id=str(raw.get("run_id") or ""),
		)


@dataclass(frozen=True)
class RunRequest:
	user_message: str
	session_id: str = ""
	stream: bool = True
	messages: list[dict[str, Any]] | None = None
	llm_options: dict[str, Any] = field(default_factory=dict)
	options: RunOptions = field(default_factory=RunOptions)
	metadata: dict[str, Any] = field(default_factory=dict)

	@classmethod
	def create(
		cls,
		user_message: str,
		session_id: str = "",
		stream: bool = True,
		messages: list[dict[str, Any]] | None = None,
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> "RunRequest":
		options = RunOptions.from_dict(run_options)
		request_metadata = dict(metadata or {})
		if options.run_id and request_metadata.get("run_id") and str(request_metadata["run_id"]) != options.run_id:
			raise ValueError("run_options.run_id conflicts with metadata.run_id")
		run_id = options.run_id or str(request_metadata.get("run_id") or "") or uuid.uuid4().hex[:16]
		request_metadata["run_id"] = run_id
		return cls(
			user_message=user_message,
			session_id=session_id,
			stream=options.stream if options.stream is not None else stream,
			messages=messages,
			llm_options=dict(llm_options or {}),
			options=options,
			metadata=request_metadata,
		)


def _positive_int(value: Any) -> int | None:
	try:
		parsed = int(value)
	except (TypeError, ValueError):
		return None
	return parsed if parsed > 0 else None
