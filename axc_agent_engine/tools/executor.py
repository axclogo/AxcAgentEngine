"""工具执行器 — 单个工具调用的重试与校验。

English: Executes one tool call with argument validation, timeout handling, and
read-only retry behavior.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.tools.context import ToolContext
from axc_agent_engine.tools.tool_output import ToolOutput

logger = logging.getLogger(__name__)

_RETRYABLE_KEYWORDS = [
	'timeout', 'timed out', 'connection', 'connect',
	'network', 'socket', 'reset', 'refused',
	'unavailable', '502', '503', '504', '429',
	'too many requests', 'rate limit',
	'temporary', 'transient', 'retry',
]

_NON_RETRYABLE_KEYWORDS = [
	'permission', 'forbidden', '403', '401',
	'unauthorized', 'invalid', 'missing',
	'not found', '404', 'bad request', '400',
	'validation', 'parameter', 'argument',
]


@dataclass
class ToolResult:
	"""工具执行结果。

	English: Normalized result of one tool execution.
	"""
	tool_call_id: str
	tool_name: str
	arguments: dict[str, Any]
	output: ToolOutput | None = None
	error: str = ""
	success: bool = True
	duration_ms: int = 0

	def compact_view(self) -> str:
		"""返回写入上下文或消息的紧凑字符串表示。

		English: Return the compact string representation stored in context/messages.
		"""
		if self.output is None:
			return ""
		return self.output.compact_view()


class ToolArgumentValidator:
	def validate(self, tool_def: ToolDefinition, arguments: dict[str, Any]) -> str | None:
		schema = tool_def.parameters
		if not schema or schema.get("type") != "object":
			return None
		required = schema.get("required", [])
		properties = schema.get("properties", {})
		for field_name in required:
			if field_name not in arguments:
				return f"Missing required parameter: {field_name}"
		for key, value in arguments.items():
			if key not in properties:
				continue
			error = self._validate_property(key, value, properties[key])
			if error:
				return error
		return None

	def _validate_property(self, key: str, value: Any, prop_schema: dict[str, Any]) -> str | None:
		expected_type = prop_schema.get("type")
		if expected_type and not _type_matches(value, expected_type):
			return f"Parameter '{key}' type mismatch: expected {expected_type}, got {type(value).__name__}"
		enum_values = prop_schema.get("enum")
		if enum_values is not None and value not in enum_values:
			return f"Parameter '{key}' must be one of {enum_values}, got {value!r}"
		if isinstance(value, str):
			min_len = prop_schema.get("minLength")
			max_len = prop_schema.get("maxLength")
			if min_len is not None and len(value) < min_len:
				return f"Parameter '{key}' too short: minimum {min_len} chars"
			if max_len is not None and len(value) > max_len:
				return f"Parameter '{key}' too long: maximum {max_len} chars"
		if isinstance(value, (int, float)) and not isinstance(value, bool):
			minimum = prop_schema.get("minimum")
			maximum = prop_schema.get("maximum")
			if minimum is not None and value < minimum:
				return f"Parameter '{key}' below minimum: {minimum}"
			if maximum is not None and value > maximum:
				return f"Parameter '{key}' above maximum: {maximum}"
		return None


class ToolRetryPolicy:
	def max_retries(self, tool_def: ToolDefinition) -> int:
		return 2 if tool_def.is_read_only else 0

	def should_retry(self, error: str) -> bool:
		return is_retryable_error(error)

	def delay(self, attempt: int) -> float:
		return 1.0 * (attempt + 1)


class SingleToolExecutor:
	def __init__(self, validator: ToolArgumentValidator | None = None, retry_policy: ToolRetryPolicy | None = None) -> None:
		self.validator = validator or ToolArgumentValidator()
		self.retry_policy = retry_policy or ToolRetryPolicy()

	async def execute(
		self,
		tool_def: ToolDefinition,
		arguments: dict[str, Any],
		tool_call_id: str = "",
		context: "ToolContext | dict[str, Any] | None" = None,
	) -> ToolResult:
		if not tool_def.execute:
			return ToolResult(
				tool_call_id=tool_call_id, tool_name=tool_def.name,
				arguments=arguments, error="Tool has no execute function", success=False,
			)
		validation_error = self.validator.validate(tool_def, arguments)
		if validation_error:
			return ToolResult(
				tool_call_id=tool_call_id, tool_name=tool_def.name,
				arguments=arguments, error=validation_error, success=False,
			)
		max_retries = self.retry_policy.max_retries(tool_def)
		result: ToolResult | None = None
		ctx_dict: dict[str, Any] = {}
		if context is not None:
			ctx_dict = context.to_dict() if isinstance(context, ToolContext) else context
		for attempt in range(max_retries + 1):
			result = await _execute_once(tool_def, arguments, tool_call_id, ctx_dict)
			if result.success:
				return result
			if attempt < max_retries and self.retry_policy.should_retry(result.error):
				logger.info(f"Tool {tool_def.name} retry #{attempt + 1} (error: {result.error})")
				await asyncio.sleep(self.retry_policy.delay(attempt))
				continue
			break
		return result  # type: ignore[return-value]


def is_retryable_error(error_str: str) -> bool:
	"""判断错误是否可重试。

	English: Decide whether an error is retryable.
	"""
	lower = error_str.lower()
	for kw in _NON_RETRYABLE_KEYWORDS:
		if kw in lower:
			return False
	for kw in _RETRYABLE_KEYWORDS:
		if kw in lower:
			return True
	return False


def _type_matches(value: Any, expected_type: str) -> bool:
	"""检查值是否匹配期望的 JSON Schema 类型。"""
	if value is None:
		return True
	if expected_type == "boolean":
		return isinstance(value, bool)
	if expected_type == "integer":
		return isinstance(value, int) and not isinstance(value, bool)
	type_map = {
		"string": str,
		"number": (int, float),
		"array": list,
		"object": dict,
	}
	expected = type_map.get(expected_type)
	if expected is None:
		return True
	return isinstance(value, expected)


async def execute_tool(
	tool_def: ToolDefinition, arguments: dict[str, Any],
	tool_call_id: str = "", context: "ToolContext | dict[str, Any] | None" = None,
) -> ToolResult:
	"""执行单个工具调用，包含参数校验和自动重试。

	English: Execute one tool call with validation and automatic retry for read-only tools.
	"""
	return await SingleToolExecutor().execute(tool_def, arguments, tool_call_id, context)


async def _execute_once(
	tool_def: ToolDefinition, arguments: dict[str, Any],
	tool_call_id: str = "", context: dict[str, Any] | None = None,
) -> ToolResult:
	"""执行一次工具调用尝试，并强制返回 ToolOutput。"""
	start = time.time()
	try:
		raw_result = await asyncio.wait_for(tool_def.execute(arguments, context or {}), timeout=tool_def.timeout)
		duration_ms = int((time.time() - start) * 1000)
	except asyncio.TimeoutError:
		return ToolResult(
			tool_call_id=tool_call_id, tool_name=tool_def.name,
			arguments=arguments, error=f"Tool execution timeout ({tool_def.timeout}s)", success=False,
			duration_ms=int((time.time() - start) * 1000),
		)
	except Exception as e:
		logger.error(f"Tool execution failed: {tool_def.name}: {e}")
		return ToolResult(
			tool_call_id=tool_call_id, tool_name=tool_def.name,
			arguments=arguments, error=str(e), success=False,
			duration_ms=int((time.time() - start) * 1000),
		)
	# 工具执行后强制校验 ToolOutput 返回类型。这是插件作者必须遵守的硬边界；
	# 上层编排器可以把异常转成 LLM 循环里的工具失败。
	if not isinstance(raw_result, ToolOutput):
		raise TypeError(f"工具必须返回 ToolOutput，实际得到 {type(raw_result).__name__}")
	if raw_result.is_error:
		return ToolResult(
			tool_call_id=tool_call_id, tool_name=tool_def.name,
			arguments=arguments, output=raw_result,
			error=raw_result._content_as_str(), success=False,
			duration_ms=duration_ms,
		)
	return ToolResult(
		tool_call_id=tool_call_id, tool_name=tool_def.name,
		arguments=arguments, output=raw_result, success=True,
		duration_ms=duration_ms,
	)
