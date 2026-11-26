"""Host-owned session operations contract. 宿主持有的会话操作契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionInfo:
	"""Summary of one host session. 宿主会话摘要。"""

	session_id: str
	agent_name: str
	title: str = ""
	status: str = "idle"
	updated_at: str = ""
	access: str = "none"
	last_message_id: str = ""
	scope: str = ""


@dataclass(frozen=True)
class SessionMessage:
	"""One durable text message in a session. 会话中的一条持久文本消息。"""

	message_id: str
	role: str
	text: str
	created_at: str = ""
	origin_session_id: str = ""


@dataclass(frozen=True)
class SessionCaller:
	"""Trusted identity of the session that invokes a tool. 调用工具的会话可信身份。"""

	session_id: str
	agent_name: str
	run_id: str = ""
	title: str = ""
	user_id: str = ""
	tenant_id: str = ""
	turn_origin: str = "user"
	provenance: dict[str, Any] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		"""Return the host-facing caller payload. 返回传给宿主的调用方数据。"""
		return {
			"session_id": self.session_id,
			"agent_name": self.agent_name,
			"run_id": self.run_id,
			"title": self.title,
			"user_id": self.user_id,
			"tenant_id": self.tenant_id,
			"turn_origin": self.turn_origin,
			"provenance": dict(self.provenance),
		}


@runtime_checkable
class SessionService(Protocol):
	"""Host-owned session storage, permissions and run control.
	宿主持有的会话存储、权限和运行控制。"""

	async def list_sessions(self, caller: dict[str, Any], filters: dict[str, Any]) -> list[SessionInfo]:
		"""List sessions visible to the caller. 列出调用方可见的会话。"""
		...

	async def read(
		self, caller: dict[str, Any], session_id: str, since: str | None, limit: int
	) -> list[SessionMessage]:
		"""Read committed text messages after a cursor. 读取游标之后的已提交文本消息。"""
		...

	async def send(self, caller: dict[str, Any], session_id: str, text: str, max_active: int) -> dict[str, Any]:
		"""Append a message and start or steer a run. 追加消息并启动或引导一次运行。"""
		...

	async def create(
		self, caller: dict[str, Any], agent_name: str, text: str, title: str, max_active: int
	) -> dict[str, Any]:
		"""Create a session and start its first run. 新建会话并启动首轮运行。"""
		...

	async def stop(self, caller: dict[str, Any], session_id: str) -> dict[str, Any]:
		"""Stop the target session's current run. 停止目标会话当前的运行。"""
		...

	async def watch(self, caller: dict[str, Any], session_id: str, enabled: bool) -> None:
		"""Subscribe or unsubscribe completion notices. 订阅或取消完成通知。"""
		...

	async def authorize(self, caller: dict[str, Any], session_id: str, level: str, cross_agent: bool) -> str:
		"""Return granted, denied or unavailable. 返回 granted、denied 或 unavailable。"""
		...
