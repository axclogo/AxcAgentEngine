"""Known write outcomes remain resumable and forkable. 已确定的写工具结果仍可恢复与分叉。"""

import asyncio

import pytest

from axc_agent_engine import EventType, InMemoryCheckpointStore, ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.config_schema import config_schema
from axc_agent_engine.tools.tool_output import ToolOutput
from tests.engine.test_session_rewind import ScriptedProvider, _response, collect, host


class WriteProbe(BasePlugin):
	name = "write_probe"
	config_schema = config_schema("write_probe", "Write probe", "Write outcome tests.", [])


def _write_tool(outcome: str, calls: list[str]) -> ToolDefinition:
	async def write(args, context):
		calls.append(context["tool_call_id"])
		if outcome == "exception":
			raise RuntimeError("write outcome unknown")
		if outcome == "timeout":
			await asyncio.Event().wait()
		return ToolOutput.error("Agent 不存在") if outcome == "error" else ToolOutput.text("written")

	return ToolDefinition(name="write_probe", execute=write, is_read_only=False, timeout=0.02)


@pytest.mark.parametrize("outcome", ["success", "error", "exception", "timeout"])
async def test_write_outcome_controls_resume_and_fork(tmp_path, monkeypatch, outcome):
	calls: list[str] = []
	tool = _write_tool(outcome, calls)
	monkeypatch.setattr(WriteProbe, "get_tools", lambda self: [tool])
	call = {"id": "write-1", "type": "function", "function": {"name": "write_probe", "arguments": "{}"}}
	provider = ScriptedProvider([_response(tool_calls=[call]), _response("done"), _response("continued")])
	store = InMemoryCheckpointStore()
	async with host(tmp_path, provider, None, store, WriteProbe) as (_, agent):
		events = await collect(agent.stream("write", session_id="s", metadata={"run_id": "r"}))
		assert events[-1].type == EventType.DONE
		assert calls == ["write-1"]
		cut = await store.latest("r")
		assert cut is not None
		if outcome in {"exception", "timeout"}:
			assert "write-1" in cut.state["unsettled_writes"]
			assert not cut.forkable
			with pytest.raises(ValueError, match="Tool outcome requires reconciliation before resume"):
				await collect(agent.resume_stream("r"))
			with pytest.raises(ValueError, match="unsettled writes"):
				await agent.fork_run("r", cut.id)
			return
		completed = [cp for cp in await store.list("r") if cp.state.get("phase") in {"round_end", "done"}]
		assert completed and all(not cp.state.get("unsettled_writes") for cp in completed)
		assert cut.forkable
		if outcome == "error":
			assert any(msg["role"] == "tool" and "Agent 不存在" in msg["content"] for msg in cut.state["messages"])
		assert (await collect(agent.resume_stream("r", "continue")))[-1].content == "continued"
		branch = await agent.fork_run("r", cut.id)
		assert (await collect(agent.resume_stream(branch.run_id)))[-1].content == "done"
		assert calls == ["write-1"]
