"""Compatibility exports for MCP client adapters."""
from .connection import MCPConnection
from .models import MCPApplicationError, MCPTool, MCPTransport, MCPTransportError
from .normalization import normalize_call_result
from .transports import JsonRpcHttpTransport, JsonRpcStdioTransport, OfficialSDKTransport

__all__ = [
	"MCPApplicationError",
	"MCPConnection",
	"MCPTool",
	"MCPTransport",
	"MCPTransportError",
	"JsonRpcHttpTransport",
	"JsonRpcStdioTransport",
	"OfficialSDKTransport",
	"normalize_call_result",
]
