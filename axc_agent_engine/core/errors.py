"""AxcAgentEngine 异常层级。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
	"""Structured error categories for policy, retry, and audit handling.
中文：此文档说明相关引擎组件的行为。"""
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
	"""Machine-readable error payload used in audit logs and API adapters.
中文：此文档说明相关引擎组件的行为。"""
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
	"""English: This documentation describes the related engine component behavior.
中文：配置错误。"""


class SchemaError(ConfigError):
	"""YAML schema 校验失败。"""


class PluginError(AxcError):
	"""English: This documentation describes the related engine component behavior.
中文：插件相关错误。"""


class PluginLoadError(PluginError):
	"""English: This documentation describes the related engine component behavior.
中文：插件加载失败。"""


class PluginInitError(PluginError):
	"""English: This documentation describes the related engine component behavior.
中文：插件初始化失败。"""


class LLMError(AxcError):
	"""LLM 调用错误。"""


class ProviderError(LLMError):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
所有 LLM provider 都失败。"""


class RetryableProviderError(ProviderError):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
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
	"""English: This documentation describes the related engine component behavior.
中文：执行器错误。"""


class MaxRoundsError(ExecutionError):
	"""English: This documentation describes the related engine component behavior.
中文：超过最大轮次。"""


class ExecutionCancelledError(ExecutionError):
	"""English: This documentation describes the related engine component behavior.
中文：执行被用户取消。"""


#English: Source note. 中文：别名
CancelledError = ExecutionCancelledError


class ExecutionTimeoutError(ExecutionError):
	"""English: This documentation describes the related engine component behavior.
中文：执行超时。"""


#English: Source note. 中文：别名
TimeoutError = ExecutionTimeoutError


class ToolError(AxcError):
	"""English: This documentation describes the related engine component behavior.
中文：工具执行错误。"""
