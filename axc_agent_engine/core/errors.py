"""AxcAgentEngine 异常层级。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
	"""Structured error categories for policy, retry, and audit handling. 供策略、重试和审计使用的错误分类。"""

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
	"""Machine-readable error payload used in audit logs and API adapters. 审计日志和 API 适配器共用的结构化错误数据。"""

	code: str
	message: str
	category: str = ErrorCategory.INTERNAL
	retryable: bool = False
	details: dict[str, Any] = field(default_factory=dict)
	cause: str = ""

	def __post_init__(self) -> None:
		object.__setattr__(self, "details", deepcopy(self.details))

	def to_dict(self) -> dict[str, Any]:
		return {
			"code": self.code,
			"message": self.message,
			"category": self.category,
			"retryable": self.retryable,
			"details": deepcopy(self.details),
			"cause": self.cause,
		}


class AxcError(Exception):
	"""engine 基础异常。"""


class ConfigError(AxcError):
	"""Invalid engine configuration. 配置错误。"""


class ConfigValueError(ValueError):
	"""Invalid configuration value. 配置值不符合要求。"""


class SchemaError(ConfigError):
	"""YAML schema 校验失败。"""


class PluginError(AxcError):
	"""Base class for plugin failures. 插件相关错误。"""


class PluginLoadError(PluginError):
	"""Plugin loading failed. 插件加载失败。"""


class PluginInitError(PluginError):
	"""Plugin initialization failed. 插件初始化失败。"""


class LLMError(AxcError):
	"""LLM 调用错误。"""


class ProviderError(LLMError):
	"""All model providers failed.
	所有 LLM provider 都失败。"""


class RetryableProviderError(ProviderError):
	"""The model call may be retried or routed to a fallback.
	可重试/可 fallback 的 provider 错误。"""


class ProviderAuthError(ProviderError):
	"""Provider 鉴权错误。"""


class ProviderBadRequestError(ProviderError):
	"""Provider 请求参数或 schema 错误。"""


class ProviderContractError(ProviderError):
	"""Provider 实现没有遵守 LLMProvider 协议。"""


class LLMTimeoutError(LLMError):
	"""LLM 调用超时。"""


class ExecutionError(AxcError):
	"""Execution failed. 执行器错误。"""


class MaxRoundsError(ExecutionError):
	"""The round limit was exceeded. 超过最大轮次。"""


class ExecutionCancelledError(ExecutionError):
	"""The caller cancelled execution. 执行被用户取消。"""


# Aliases 别名
CancelledError = ExecutionCancelledError


class ExecutionTimeoutError(ExecutionError):
	"""Execution timed out. 执行超时。"""


# Aliases 别名
TimeoutError = ExecutionTimeoutError


class ToolError(AxcError):
	"""Tool execution failed. 工具执行错误。"""
