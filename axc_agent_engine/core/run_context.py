"""Run context normalization helpers.
中文：运行上下文归一化辅助函数。"""
from __future__ import annotations

import uuid
from typing import Any, Callable
from copy import deepcopy


ContextFactory = Callable[..., dict[str, Any]]
RUNTIME_RUN_OPTION_KEYS = frozenset({"approval_queue", "response_queue"})


def dict_or_empty(value: dict | None, name: str) -> dict[str, Any]:
	if value is None:
		return {}
	if not isinstance(value, dict):
		raise TypeError(f"{name} must be a dict")
	return dict(value)


def copy_run_options(value: dict | None, name: str = "run_options") -> dict[str, Any]:
	options = dict_or_empty(value, name)
	return {
		key: option if key in RUNTIME_RUN_OPTION_KEYS else deepcopy(option)
		for key, option in options.items()
	}


def context_run_id(context: dict[str, Any]) -> str:
	if "run_id" not in context or context["run_id"] in (None, ""):
		return ""
	return str(context["run_id"])


def call_context_factory(factory: ContextFactory | None, name: str, *args: Any) -> dict[str, Any]:
	if not factory:
		return {}
	value = factory(*args)
	if not isinstance(value, dict):
		raise TypeError(f"{name} must return a dict")
	return dict(value)


def sync_run_id(options: dict[str, Any], metadata: dict[str, Any]) -> None:
	option_run_id = context_run_id(options)
	metadata_run_id = context_run_id(metadata)
	if option_run_id and metadata_run_id and option_run_id != metadata_run_id:
		raise ValueError("run_options.run_id conflicts with metadata.run_id")
	if option_run_id:
		metadata["run_id"] = option_run_id
	elif metadata_run_id:
		metadata["run_id"] = metadata_run_id


def normalize_run_context(
	run_options: dict | None = None,
	metadata: dict | None = None,
	*,
	default_run_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
	options = dict_or_empty(run_options, "run_options")
	normalized_metadata = dict_or_empty(metadata, "metadata")
	sync_run_id(options, normalized_metadata)
	if not context_run_id(normalized_metadata):
		normalized_metadata["run_id"] = default_run_id or uuid.uuid4().hex[:16]
	return options, normalized_metadata
