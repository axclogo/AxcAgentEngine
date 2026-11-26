"""Run context normalization helpers.
中文：运行上下文归一化辅助函数。"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import Any

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
	return {key: option if key in RUNTIME_RUN_OPTION_KEYS else deepcopy(option) for key, option in options.items()}


def checkpoint_owner(metadata: dict) -> dict:
	"""Identity fields that stay bound to a checkpoint. 随检查点绑定的归属字段。"""
	return {key: metadata[key] for key in ("tenant_id", "user_id") if key in metadata}


def checkpoint_owner_conflict(owner: dict, metadata: dict, *, exact: bool = False) -> str:
	"""Explain an owner mismatch before fork or resume. 在分叉或恢复前说明归属冲突。

	Returns an empty string when the caller may use the checkpoint.
	调用方可以使用切点时返回空字符串。"""
	if not isinstance(owner, dict):
		raise TypeError("Checkpoint owner must be a dict")
	supplied = {key: value for key, value in checkpoint_owner(metadata).items() if value not in ("", None)}
	saved = {key: value for key, value in owner.items() if value not in ("", None)}
	if not saved:
		if supplied:
			return "Checkpoint has no owner record; retry without tenant or user identity"
		return ""
	if (
		(saved != supplied)
		if exact
		else any(key in supplied and supplied[key] != value for key, value in saved.items())
	):
		return "Checkpoint belongs to another tenant or user"
	return ""


def context_run_id(context: dict[str, Any]) -> str:
	run_id = context.get("run_id")
	if run_id is None:
		return ""
	if not isinstance(run_id, str) and type(run_id) is not int:
		raise TypeError("run_id must be a string or integer")
	return str(run_id)


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
