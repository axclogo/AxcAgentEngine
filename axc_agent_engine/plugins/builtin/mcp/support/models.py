"""MCP client data models and errors."""
from dataclasses import dataclass, field
from typing import Any, Protocol


DEFAULT_PROTOCOL_VERSION = "2024-11-05"


@dataclass(frozen=True)
class MCPTool:
	name: str
	description: str = ""
	input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
	annotations: dict[str, Any] = field(default_factory=dict)


class MCPTransportError(RuntimeError):
	"""Transport-level failure that may be retried safely for read-only calls."""


class MCPApplicationError(RuntimeError):
	"""Server-side JSON-RPC application error; do not retry implicitly."""


class MCPTransport(Protocol):
	async def connect(self) -> None: ...
	async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...
	async def close(self) -> None: ...
