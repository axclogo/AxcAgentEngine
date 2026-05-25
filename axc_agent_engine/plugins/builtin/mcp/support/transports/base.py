"""Shared MCP transport helpers."""
import importlib
import os
from typing import Any


def timeout(config: dict[str, Any], key: str, default: float) -> float:
	try:
		value = float(config.get(key, default))
	except (TypeError, ValueError):
		return default
	return value if value > 0 else default


def module_exists(name: str) -> bool:
	try:
		importlib.import_module(name)
		return True
	except ImportError:
		return False


def client_session_class() -> Any:
	try:
		return getattr(importlib.import_module("mcp"), "ClientSession")
	except (ImportError, AttributeError):
		return getattr(importlib.import_module("mcp.client.session"), "ClientSession")


def call_transport_client(factory: Any, url: str, headers: dict[str, str] | None = None) -> Any:
	try:
		return factory(url, headers=headers)
	except TypeError:
		return factory(url)


def merge_env(env: dict[str, str] | None) -> dict[str, str] | None:
	if env is None:
		return None
	merged = dict(os.environ)
	merged.update({str(key): str(value) for key, value in env.items()})
	return merged
