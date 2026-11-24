"""Normalized per-run request objects. 规范化后的单次运行请求。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from axc_agent_engine.core.run_context import context_run_id, dict_or_empty, normalize_run_context


@dataclass(frozen=True)
class RunOptions:
	stream_idle_timeout: int | None = None
	approval_queue: Any = None
	response_queue: Any = None
	stream: bool | None = None
	run_id: str = ""

	@classmethod
	def from_dict(cls, raw: dict | None) -> RunOptions:
		raw = dict_or_empty(raw, "run_options")
		return cls.from_normalized(raw)

	@classmethod
	def from_normalized(cls, raw: dict[str, Any]) -> RunOptions:
		options = cls(**{**raw, "run_id": context_run_id(raw)})
		if "stream" in raw and type(options.stream) is not bool:
			raise TypeError("run_options.stream must be a boolean")
		timeout = options.stream_idle_timeout
		if "stream_idle_timeout" in raw and (type(timeout) is not int or timeout <= 0):
			raise ValueError("run_options.stream_idle_timeout must be a positive integer")
		return options


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
	) -> RunRequest:
		request_run_options, request_metadata = normalize_run_context(run_options, metadata)
		request_llm_options = dict_or_empty(llm_options, "llm_options")
		options = RunOptions.from_normalized(request_run_options)
		return cls(
			user_message=user_message,
			session_id=session_id,
			stream=options.stream if options.stream is not None else stream,
			messages=deepcopy(messages),
			llm_options=deepcopy(request_llm_options),
			options=options,
			metadata=deepcopy(request_metadata),
		)
