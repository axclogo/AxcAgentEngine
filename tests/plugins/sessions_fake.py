"""Test double for the host session service. 宿主会话服务的测试替身。"""

from __future__ import annotations

from typing import Any

from axc_agent_engine.plugins.builtin.sessions.protocol import SessionInfo, SessionMessage


class FakeSessionService:
	def __init__(self) -> None:
		self.sessions = {
			"s2": SessionInfo("s2", "agent-b", "客户访谈", "idle", access="none"),
			"s3": SessionInfo("s3", "agent-b", "分析报告", "running", access="read"),
			"s4": SessionInfo("s4", "agent-a", "周报", "idle", access="operate"),
		}
		self.messages = {
			"s2": [
				SessionMessage(f"m{index}", "user" if index % 2 else "assistant", f"消息 {index}")
				for index in range(1, 6)
			]
		}
		self.calls: list[tuple[str, Any]] = []
		self.sent: list[tuple[str, str, int]] = []
		self.created: list[tuple[str, str, str, int]] = []
		self.watches: list[tuple[str, bool]] = []
		self.authorize_calls: list[tuple[str, str]] = []
		self.decisions: dict[tuple[str, str], str] = {}

	async def list_sessions(self, caller: dict, filters: dict) -> list[SessionInfo]:
		self.calls.append(("list", dict(filters)))
		rows = list(self.sessions.values())
		if name := filters.get("agent_name"):
			rows = [row for row in rows if row.agent_name == name]
		if status := filters.get("status"):
			rows = [row for row in rows if row.status == status]
		if title := filters.get("title"):
			rows = [row for row in rows if title in row.title]
		return rows[: int(filters.get("limit", 20))]

	async def read(self, caller: dict, session_id: str, since: str | None, limit: int) -> list[SessionMessage]:
		self.calls.append(("read", session_id, since, limit))
		items = self.messages.get(session_id, [])
		if since is not None:
			ids = [message.message_id for message in items]
			if since not in ids:
				raise LookupError(f"unknown message: {since}")
			items = items[ids.index(since) + 1 :]
		return items[-limit:]

	async def send(self, caller: dict, session_id: str, text: str, max_active: int) -> dict:
		self.calls.append(("send", session_id, text, max_active))
		if session_id not in self.sessions:
			raise LookupError(f"unknown session: {session_id}")
		self.sent.append((session_id, text, max_active))
		return {"accepted": True, "message_id": f"m-{len(self.sent)}"}

	async def create(self, caller: dict, agent_name: str, text: str, title: str, max_active: int) -> dict:
		self.calls.append(("create", agent_name, text, title, max_active))
		self.created.append((agent_name, text, title, max_active))
		return {"accepted": True, "session_id": "s-new"}

	async def stop(self, caller: dict, session_id: str) -> dict:
		self.calls.append(("stop", session_id))
		row = self.sessions.get(session_id)
		if row is None:
			raise LookupError(f"unknown session: {session_id}")
		return {"stopped": row.status == "running", "status": row.status}

	async def watch(self, caller: dict, session_id: str, enabled: bool) -> None:
		self.calls.append(("watch", session_id, enabled))
		self.watches.append((session_id, enabled))

	async def authorize(self, caller: dict, session_id: str, level: str, cross_agent: bool) -> str:
		self.authorize_calls.append((session_id, level))
		return self.decisions.get((session_id, level), "granted")
