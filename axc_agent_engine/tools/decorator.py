"""@tool 装饰器 — 生成带自动参数校验的类型化工具定义。

所有被装饰的工具都必须返回 ToolOutput。返回其他类型的函数会在注册时被拒绝。
"""
import functools
import inspect
from typing import Any, Callable, Coroutine, get_type_hints

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.tools.tool_output import ToolOutput

_PYTHON_TYPE_TO_JSON = {
	str: "string",
	int: "integer",
	float: "number",
	bool: "boolean",
	list: "array",
	dict: "object",
}


def tool(
	name: str = "",
	description: str = "",
	is_read_only: bool = False,
	timeout: int = 120,
	deferred: bool = False,
) -> Callable:
	"""从带类型标注的 async 函数创建 ToolDefinition。

	用法：
		@tool(name="my_tool", description="执行某个操作")
		async def my_tool(path: str, content: str = "") -> ToolOutput:
			return ToolOutput.text("结果")

	被装饰函数必须返回 ToolOutput；其他返回类型会在装饰阶段抛 TypeError。
	"""
	def decorator(fn: Callable[..., Coroutine]) -> Callable:
		tool_name = name or fn.__name__
		tool_desc = description or (fn.__doc__ or "").strip().split("\n")[0]
		parameters = _build_parameters_schema(fn)
		# 校验返回类型标注
		hints = get_type_hints(fn)
		return_type = hints.get("return")
		if return_type is not None and return_type is not ToolOutput:
			raise TypeError(
				f"工具 '{tool_name}' 必须返回 ToolOutput，"
				f"实际返回标注为：{return_type}"
			)

		async def execute(args: dict, context: dict) -> ToolOutput:
			sig = inspect.signature(fn)
			bound_args = {}
			for param_name, param in sig.parameters.items():
				if param_name in ("args", "context"):
					continue
				if param_name in args:
					bound_args[param_name] = args[param_name]
				elif param.default is not inspect.Parameter.empty:
					bound_args[param_name] = param.default
			return await fn(**bound_args)

		tool_def = ToolDefinition(
			name=tool_name,
			description=tool_desc,
			parameters=parameters,
			execute=execute,
			is_read_only=is_read_only,
			timeout=timeout,
			deferred=deferred,
		)

		@functools.wraps(fn)
		async def wrapper(*a, **kw):
			return await fn(*a, **kw)

		wrapper.tool_definition = tool_def
		return wrapper

	return decorator


def _build_parameters_schema(fn: Callable) -> dict[str, Any]:
	"""根据函数类型标注构建 JSON Schema。"""
	hints = get_type_hints(fn)
	sig = inspect.signature(fn)
	properties: dict[str, Any] = {}
	required: list[str] = []
	for param_name, param in sig.parameters.items():
		if param_name in ("args", "context", "self", "cls"):
			continue
		param_type = hints.get(param_name, str)
		json_type = _PYTHON_TYPE_TO_JSON.get(param_type, "string")
		prop: dict[str, Any] = {"type": json_type}
		properties[param_name] = prop
		if param.default is inspect.Parameter.empty:
			required.append(param_name)
		else:
			prop["default"] = param.default
	schema: dict[str, Any] = {"type": "object", "properties": properties}
	if required:
		schema["required"] = required
	return schema
