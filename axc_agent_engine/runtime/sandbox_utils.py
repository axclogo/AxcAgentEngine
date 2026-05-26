"""Shared helpers for sandbox executors.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import os
import sys

try:
	import resource

	def _set_limits() -> None:
		try:
			resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
		except (ValueError, OSError):
			pass
		try:
			resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
		except (ValueError, OSError):
			pass
		try:
			resource.setrlimit(resource.RLIMIT_FSIZE, (100 * 1024 * 1024, 100 * 1024 * 1024))
		except (ValueError, OSError):
			pass
except ImportError:
	_set_limits = None  # type: ignore[assignment]


SAFE_ENV_KEYS = ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR")


def build_env(overrides: dict[str, str]) -> dict[str, str]:
	env = {key: value for key in SAFE_ENV_KEYS if (value := os.environ.get(key))}
	for key, value in overrides.items():
		if key in SAFE_ENV_KEYS:
			env[key] = value
	return env


def decode_limited(data: bytes, limit: int) -> tuple[str, bool]:
	if limit > 0 and len(data) > limit:
		return data[:limit].decode("utf-8", errors="replace"), True
	return data.decode("utf-8", errors="replace"), False


def subprocess_preexec_fn():
	return _set_limits if sys.platform != "win32" else None


def write_text(path: str, content: str) -> None:
	with open(path, "w", encoding="utf-8") as f:
		f.write(content)
