"""AxcAgentEngine 异常层级。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
	"""Structured error categories for policy, retry, and audit handling."""
	CONFIG = "config"
	SCHEMA = "schema"
	PLUGIN = "plugin"
	PROVIDER = "provider"
	TIMEOUT = "timeout"
	CANCELLED = "cancelled"
	TOOL = "tool"
	POLICY = "policy"
	CONTRACT = "contract"
	INTERNAL = "internal"


@dataclass(frozen=True)
class ErrorEnvelope:
	"""Machine-readable error payload used in audit logs and API adapters."""
	code: str
	message: str
	category: str = ErrorCategory.INTERNAL
	retryable: bool = False
	details: dict[str, Any] = field(default_factory=dict)
	cause: str = ""

	def to_dict(self) -> dict[str, Any]:
		return {
			"code": self.code,
			"message": self.message,
			"category": self.category,
			"retryable": self.retryable,
			"details": self.details,
			"cause": self.cause,
		}


class AxcError(Exception):
	"""engine 基础异常。"""


class ConfigError(AxcError):
	"""配置错误。"""


class SchemaError(ConfigError):
	"""YAML schema 校验失败。"""


class PluginError(AxcError):
	"""插件相关错误。"""


class PluginLoadError(PluginError):
	"""插件加载失败。"""


class PluginInitError(PluginError):
	"""插件初始化失败。"""


class LLMError(AxcError):
	"""LLM 调用错误。"""


class ProviderError(LLMError):
	"""所有 LLM provider 都失败。"""


class RetryableProviderError(ProviderError):
	"""可重试/可 fallback 的 provider 错误。"""


class ProviderAuthError(ProviderError):
	"""Provider 鉴权错误。"""


class ProviderBadRequestError(ProviderError):
	"""Provider 请求参数或 schema 错误。"""


class ProviderContractError(ProviderError):
	"""Provider 实现没有遵守 LLMProvider 协议。"""


class LLMTimeoutError(LLMError):
	"""LLM 调用超时。"""


class ExecutionError(AxcError):
	"""执行器错误。"""


class MaxRoundsError(ExecutionError):
	"""超过最大轮次。"""


class ExecutionCancelledError(ExecutionError):
	"""执行被用户取消。"""


# 别名
CancelledError = ExecutionCancelledError


class ExecutionTimeoutError(ExecutionError):
	"""执行超时。"""


# 别名
TimeoutError = ExecutionTimeoutError


class ToolError(AxcError):
	"""工具执行错误。"""
