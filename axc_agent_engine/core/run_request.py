"""Normalized per-run request objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunOptions:
	stream_idle_timeout: int | None = None
	approval_queue: Any = None
	response_queue: Any = None
	stream: bool | None = None

	@classmethod
	def from_dict(cls, raw: dict | None) -> "RunOptions":
		raw = raw or {}
		return cls(
			stream_idle_timeout=_positive_int(raw.get("stream_idle_timeout")),
			approval_queue=raw.get("approval_queue"),
			response_queue=raw.get("response_queue"),
			stream=raw.get("stream") if isinstance(raw.get("stream"), bool) else None,
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
		return cls(
			user_message=user_message,
			session_id=session_id,
			stream=options.stream if options.stream is not None else stream,
			messages=messages,
			llm_options=dict(llm_options or {}),
			options=options,
			metadata=dict(metadata or {}),
		)


def _positive_int(value: Any) -> int | None:
	try:
		parsed = int(value)
	except (TypeError, ValueError):
		return None
	return parsed if parsed > 0 else None
