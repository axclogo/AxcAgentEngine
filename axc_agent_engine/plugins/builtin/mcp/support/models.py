"""MCP client data models and errors. MCP 客户端的数据结构和错误类型。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MCPTool:
	name: str
	description: str = ""
	input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
	annotations: dict[str, Any] = field(default_factory=dict)


class MCPTransportError(RuntimeError):
	"""Transport-level failure that may be retried safely for read-only calls. 只读调用可安全重试的传输层故障。"""


class MCPApplicationError(RuntimeError):
	"""Server-side JSON-RPC application error; do not retry implicitly. 服务端 JSON-RPC 应用错误应直接上报。"""
