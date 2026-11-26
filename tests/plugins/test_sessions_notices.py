"""Tests for session notice formatting and wakeup decisions. 会话通知与唤醒决策测试。"""

from __future__ import annotations

import pytest

from axc_agent_engine.plugins.builtin.sessions import (
	CALLER_STATES,
	NOTICE_STATUSES,
	decide,
	format_notice,
	notice_id,
)


def test_notice_id_format_and_validation() -> None:
	assert notice_id("sess-b", "run-1") == "notice:sess-b:run-1"
	for args in (("", "run"), ("sess", " "), (1, "run")):
		with pytest.raises(ValueError):
			notice_id(*args)


@pytest.mark.parametrize(
	"status,label",
	[("done", "已完成"), ("cancelled", "已停止"), ("error", "出错")],
)
def test_format_notice_statuses(status: str, label: str) -> None:
	text = format_notice(title="客户访谈", agent_name="情报助手", status=status, last_reply="第三节已改好")
	assert text == (
		f"后台会话「客户访谈」（情报助手）{label}。\n第三节已改好\n除非你继续给它发消息，否则它不会再做别的事。"
	)


def test_format_notice_empty_reply_and_truncation() -> None:
	assert "（没有回复内容）" in format_notice(title="t", agent_name="a", status="done", last_reply="  ")
	exact = "x" * 10
	text = format_notice(title="t", agent_name="a", status="done", last_reply=exact, max_chars=10)
	assert "…（已截断）" not in text
	text = format_notice(title="t", agent_name="a", status="done", last_reply=exact + "y", max_chars=10)
	assert "x" * 10 + "…（已截断）" in text


def test_format_notice_rejects_invalid_arguments() -> None:
	with pytest.raises(ValueError):
		format_notice(title="t", agent_name="a", status="other", last_reply="x")
	with pytest.raises(ValueError):
		format_notice(title="t", agent_name="a", status="done", last_reply="x", max_chars=-1)
	with pytest.raises(TypeError):
		format_notice(title="t", agent_name="a", status="done", last_reply=1)


@pytest.mark.parametrize(
	"caller_state,auto,max_wakeups,expected",
	[
		("archived", 0, 10, "drop"),
		("deleted", 0, 10, "drop"),
		("running", 0, 10, "inject"),
		("running", 9, 10, "inject"),
		("running", 10, 10, "hold"),
		("running", 100000, 0, "inject"),
		("idle", 0, 0, "start"),
		("idle", 1000, 0, "start"),
		("idle", 9, 10, "start"),
		("idle", 10, 10, "hold"),
	],
)
def test_decide_rules(caller_state: str, auto: int, max_wakeups: int, expected: str) -> None:
	assert decide(caller_state=caller_state, auto_wakeups=auto, max_wakeups=max_wakeups) == expected


def test_decide_rejects_invalid_arguments() -> None:
	with pytest.raises(ValueError):
		decide(caller_state="other", auto_wakeups=0, max_wakeups=1)
	with pytest.raises(ValueError):
		decide(caller_state="idle", auto_wakeups=-1, max_wakeups=1)
	with pytest.raises(ValueError):
		decide(caller_state="idle", auto_wakeups=0, max_wakeups=-1)
	with pytest.raises(ValueError):
		decide(caller_state="idle", auto_wakeups="0", max_wakeups=1)


def test_notice_constants_are_exported() -> None:
	assert NOTICE_STATUSES == ("done", "cancelled", "error")
	assert CALLER_STATES == ("idle", "running", "archived", "deleted")
