"""Session completion notices. 会话完成通知的文案与唤醒决策。"""

from __future__ import annotations

NOTICE_STATUSES = ("done", "cancelled", "error")
CALLER_STATES = ("idle", "running", "archived", "deleted")

_STATUS_LABELS = {"done": "已完成", "cancelled": "已停止", "error": "出错"}


def notice_id(target_session_id: str, target_run_id: str) -> str:
	"""Build the stable notice message id. 生成稳定的通知消息 ID。"""
	for value, name in ((target_session_id, "target_session_id"), (target_run_id, "target_run_id")):
		if not isinstance(value, str) or not value.strip():
			raise ValueError(f"{name} must be a nonempty string")
	return f"notice:{target_session_id}:{target_run_id}"


def format_notice(
	*,
	title: str,
	agent_name: str,
	status: str,
	last_reply: str,
	max_chars: int = 4000,
) -> str:
	"""Render one completion notice for the caller session. 为发起方会话渲染完成通知。"""
	if not isinstance(title, str) or not isinstance(agent_name, str):
		raise TypeError("title and agent_name must be strings")
	if status not in NOTICE_STATUSES:
		raise ValueError(f"status must be one of {NOTICE_STATUSES}")
	if not isinstance(last_reply, str):
		raise TypeError("last_reply must be a string")
	if type(max_chars) is not int or max_chars < 0:
		raise ValueError("max_chars must be a nonnegative integer")
	if last_reply.strip():
		reply = last_reply if len(last_reply) <= max_chars else f"{last_reply[:max_chars]}…（已截断）"
	else:
		reply = "（没有回复内容）"
	return "\n".join(
		[
			f"后台会话「{title}」（{agent_name}）{_STATUS_LABELS[status]}。",
			reply,
			"除非你继续给它发消息，否则它不会再做别的事。",
		]
	)


def decide(*, caller_state: str, auto_wakeups: int, max_wakeups: int) -> str:
	"""Choose how the host delivers one notice. 选择宿主投递一条通知的方式。

	Every notice that makes the caller execute another step increments
	auto_wakeups, whether it starts a new turn or injects into the running one.
	hold writes the notice but does not deliver it or start a turn.
	每条让发起方多执行一步的通知都计入 auto_wakeups，start 和 inject 都算；
	hold 只写入记录，不送入当前运行也不开启新一轮。"""
	if caller_state not in CALLER_STATES:
		raise ValueError(f"caller_state must be one of {CALLER_STATES}")
	for value, name in ((auto_wakeups, "auto_wakeups"), (max_wakeups, "max_wakeups")):
		if type(value) is not int or value < 0:
			raise ValueError(f"{name} must be a nonnegative integer")
	if caller_state in {"archived", "deleted"}:
		return "drop"
	if max_wakeups == 0 or auto_wakeups < max_wakeups:
		return "inject" if caller_state == "running" else "start"
	return "hold"
