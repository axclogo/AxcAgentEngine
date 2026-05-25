"""HTTP builtin tool and SSRF policy."""
import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from axc_agent_engine.tools.tool_output import ToolOutput

from .result_store import ResultStoreReader
from .support import bounded_int, truncate_by_bytes

BLOCKED_HTTP_HOSTS = frozenset({"localhost", "metadata.google.internal"})
MAX_TOOL_TIMEOUT = 600
DEFAULT_HTTP_MAX_BYTES = 2000
MAX_HTTP_BYTES = 5 * 1024 * 1024


def is_blocked_ip(ip: str) -> bool:
	"""Return True for local, private, metadata, or otherwise unsafe IP ranges."""
	try:
		addr = ipaddress.ip_address(ip)
	except ValueError:
		return True
	return not addr.is_global


def resolve_host_ips(hostname: str) -> list[str]:
	"""Resolve host to IP strings for SSRF checks."""
	infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
	return sorted({info[4][0] for info in infos})


class BuiltinHttpPolicy:
	async def validate_url(self, url: str) -> str | None:
		parsed = urlparse(url)
		if parsed.scheme not in {"http", "https"}:
			return "Only http and https URLs are allowed"
		if not parsed.hostname:
			return "URL hostname is required"
		hostname = parsed.hostname.rstrip(".").lower()
		if hostname in BLOCKED_HTTP_HOSTS or hostname.endswith(".local"):
			return f"Blocked unsafe host: {parsed.hostname}"
		try:
			ip_obj = ipaddress.ip_address(hostname)
			if is_blocked_ip(str(ip_obj)):
				return f"Blocked unsafe IP address: {hostname}"
			return None
		except ValueError:
			pass
		try:
			ips = await asyncio.to_thread(resolve_host_ips, hostname)
		except socket.gaierror:
			return f"Failed to resolve host: {parsed.hostname}"
		except OSError as e:
			return f"Failed to validate host: {e}"
		for resolved_ip in ips:
			if is_blocked_ip(resolved_ip):
				return f"Blocked unsafe resolved IP address: {resolved_ip}"
		return None


class BuiltinHttpTools:
	def __init__(
		self,
		httpx_module: Any,
		http_policy: BuiltinHttpPolicy | None = None,
		result_reader: ResultStoreReader | None = None,
	) -> None:
		self._httpx = httpx_module
		self._http_policy = http_policy or BuiltinHttpPolicy()
		self._result_reader = result_reader or ResultStoreReader()

	async def request(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		url = args.get("url", "")
		if not url:
			return ToolOutput.error("url cannot be empty")
		url_error = await self._http_policy.validate_url(url)
		if url_error:
			return ToolOutput.error(url_error)
		timeout = bounded_int(args.get("timeout", 30), 1, MAX_TOOL_TIMEOUT, 30)
		max_bytes = bounded_int(args.get("max_bytes", DEFAULT_HTTP_MAX_BYTES), 1, MAX_HTTP_BYTES, DEFAULT_HTTP_MAX_BYTES)
		try:
			async with self._httpx.AsyncClient(timeout=timeout) as client:
				resp = await client.request(
					args.get("method", "GET").upper(),
					url,
					headers=args.get("headers", {}),
					json=args.get("body") if args.get("body") else None,
				)
				body = resp.text
				artifacts = []
				body_preview = truncate_by_bytes(body, max_bytes)
				artifact_id = ""
				result_store = self._result_reader.store(context)
				if result_store and len(body.encode()) > max_bytes:
					ref = await result_store.put(body, {"kind": "text", "url": url})
					artifacts.append(ref)
					artifact_id = ref.id
				content_data: dict[str, Any] = {
					"status": resp.status_code,
					"headers": dict(resp.headers),
					"content_type": resp.headers.get("content-type", ""),
					"body_preview": body_preview,
					"truncated": len(body.encode()) > max_bytes,
					"max_bytes": max_bytes,
				}
				if artifact_id:
					content_data["body_artifact_id"] = artifact_id
				summary = f"HTTP {resp.status_code} 来自 {url}（{len(body)} 字节）"
				return ToolOutput(content=content_data, content_type="json", summary=summary, artifacts=artifacts)
		except Exception as e:
			return ToolOutput.error(str(e))
