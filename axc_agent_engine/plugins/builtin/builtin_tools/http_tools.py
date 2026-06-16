"""HTTP builtin tool and SSRF policy.
中文：此文档说明相关引擎组件的行为。"""
import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from axc_agent_engine.tools.tool_output import ArtifactRef, ToolOutput

from .artifact_store import ArtifactStoreReader
from .support import bounded_int

BLOCKED_HTTP_HOSTS = frozenset({"localhost", "metadata.google.internal"})
MAX_TOOL_TIMEOUT = 600
DEFAULT_HTTP_MAX_BYTES = 2000
MAX_HTTP_BYTES = 5 * 1024 * 1024
HTTP_RESULT_EXTERNALIZE_BYTES = 10 * 1024 * 1024


def is_blocked_ip(ip: str) -> bool:
	"""Return True for local, private, metadata, or otherwise unsafe IP ranges.
中文：此文档说明相关引擎组件的行为。"""
	try:
		addr = ipaddress.ip_address(ip)
	except ValueError:
		return True
	return not addr.is_global


def resolve_host_ips(hostname: str) -> list[str]:
	"""Resolve host to IP strings for SSRF checks.
中文：此文档说明相关引擎组件的行为。"""
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
		artifact_reader: ArtifactStoreReader | None = None,
	) -> None:
		self._httpx = httpx_module
		self._http_policy = http_policy or BuiltinHttpPolicy()
		self._artifact_reader = artifact_reader or ArtifactStoreReader()

	async def request(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		url = args.get("url", "")
		if not url:
			return ToolOutput.error("url cannot be empty")
		url_error = await self._http_policy.validate_url(url)
		if url_error:
			return ToolOutput.error(url_error)
		timeout = bounded_int(args.get("timeout", 30), 1, MAX_TOOL_TIMEOUT, 30)
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
				artifact_store = self._artifact_reader.store(context)
				artifact_id = ""
				if artifact_store and len(body.encode()) > HTTP_RESULT_EXTERNALIZE_BYTES:
					ref = await artifact_store.put_text(body, {"url": url}, kind="text")
					artifacts.append(ref)
					artifact_id = ref.id
				content_data: dict[str, Any] = {
					"status": resp.status_code,
					"headers": dict(resp.headers),
					"content_type": resp.headers.get("content-type", ""),
					"body": "" if artifact_id else body,
					"externalized": bool(artifact_id),
				}
				if artifact_id:
					content_data["body_artifact_id"] = artifact_id
				summary = f"HTTP {resp.status_code} 来自 {url}（{len(body)} 字节）"
				llm_view = _http_llm_view(url, content_data, artifacts[0] if artifacts else None)
				return ToolOutput(content=content_data, content_type="json", summary=summary, llm_view=llm_view, artifacts=artifacts)
		except Exception as e:
			return ToolOutput.error(str(e))


def _http_llm_view(url: str, content: dict[str, Any], artifact: ArtifactRef | None) -> str:
	lines = [
		f"http_request {url}",
		f"status: {content['status']}",
		f"content_type: {content.get('content_type', '')}",
	]
	if artifact:
		lines.extend([
			"完整响应体已外部化。",
			f"artifact_id: {artifact.id}",
			f"size: {artifact.size} bytes",
			"内容已完整外部化；请用 artifact_read/artifact_page 按需读取。",
		])
	else:
		lines.extend(["body:", str(content.get("body", ""))])
	return "\n".join(lines)
