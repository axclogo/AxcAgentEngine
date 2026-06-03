from __future__ import annotations

from types import SimpleNamespace

import pytest

from axc_agent_engine.core.dispatcher import AgentEnvelope
from axc_agent_engine.sidecar.multi_agent.session import AgentOrchestrationWorker, MultiAgentEventSink, MultiAgentSession
from axc_agent_engine.sidecar.multi_agent.types import MultiAgentEventType, SessionMode


class ReplyDispatcher:
	def __init__(self, replies: dict[str, str] | None = None, fail: set[str] | None = None) -> None:
		self.replies = replies or {}
		self.fail = fail or set()
		self.envelopes: list[AgentEnvelope] = []

	async def request(self, envelope: AgentEnvelope, timeout: float = 60.0) -> AgentEnvelope:
		self.envelopes.append(envelope)
		if envelope.recipient in self.fail:
			return AgentEnvelope(
				sender=envelope.recipient,
				recipient=envelope.sender,
				type="error",
				content=f"{envelope.recipient} failed",
				conversation_id=envelope.conversation_id,
				trace_id=envelope.trace_id,
			)
		return AgentEnvelope(
			sender=envelope.recipient,
			recipient=envelope.sender,
			type="reply",
			content=self.replies.get(envelope.recipient, f"{envelope.recipient} says hi"),
			conversation_id=envelope.conversation_id,
			trace_id=envelope.trace_id,
		)


class StopAfterRound:
	def __init__(self, stop_round: int = 1, reason: str = "done") -> None:
		self.stop_round = stop_round
		self.reason = reason

	async def should_stop(self, ctx, round_num: int):
		return (round_num >= self.stop_round, self.reason if round_num >= self.stop_round else "")


def _agent(name: str):
	return SimpleNamespace(name=name)


@pytest.mark.asyncio
async def test_multi_agent_session_streams_round_messages_and_stats():
	dispatcher = ReplyDispatcher({"alpha": "first", "beta": "second"})
	session = MultiAgentSession(
		[_agent("alpha"), _agent("beta")],
		dispatcher,
		mode=SessionMode.GROUP_CHAT,
		topic="topic",
		stop_condition=StopAfterRound(1),
	)

	events = [event async for event in session.stream()]

	assert [event.type for event in events] == [
		MultiAgentEventType.ROUND_START,
		MultiAgentEventType.MESSAGE,
		MultiAgentEventType.MESSAGE,
		MultiAgentEventType.ROUND_END,
		MultiAgentEventType.ROUND_START,
		MultiAgentEventType.DONE,
	]
	assert [event.content for event in events if event.type == MultiAgentEventType.MESSAGE] == ["first", "second"]
	assert events[-1].metadata["total_speaks"] == 2
	assert events[-1].metadata["agent_speaks"] == {"alpha": 1, "beta": 1}
	assert [envelope.recipient for envelope in dispatcher.envelopes] == ["alpha", "beta"]
	assert dispatcher.envelopes[0].sender == "session"


@pytest.mark.asyncio
async def test_multi_agent_session_run_collects_messages_and_errors():
	dispatcher = ReplyDispatcher({"alpha": "ok"}, fail={"beta"})
	session = MultiAgentSession(
		[_agent("alpha"), _agent("beta")],
		dispatcher,
		mode=SessionMode.GROUP_CHAT,
		topic="topic",
		stop_condition=StopAfterRound(1),
	)

	result = await session.run()

	assert "[alpha] ok" in result
	assert "[错误] beta failed" in result


@pytest.mark.asyncio
async def test_multi_agent_session_done_when_no_speakers_and_manual_stop():
	empty = MultiAgentSession([], ReplyDispatcher(), scheduler=SimpleNamespace(
		select_speakers=lambda ctx, agents, step: [],
		steps_per_round=lambda agents: 1,
	), stop_condition=StopAfterRound(99))
	manual = MultiAgentSession([_agent("a")], ReplyDispatcher(), stop_condition=StopAfterRound(99))
	manual.stop()

	empty_events = [event async for event in empty.stream()]
	manual_events = [event async for event in manual.stream()]

	assert empty_events[-1].content == "无可用发言者"
	assert manual_events[-1].content == "手动停止"


@pytest.mark.asyncio
async def test_multi_agent_session_social_feed_parses_json_and_plain_posts():
	dispatcher = ReplyDispatcher({"alpha": '{"type":"post","content":"json"}', "beta": "plain"})
	session = MultiAgentSession(
		[_agent("alpha"), _agent("beta")],
		dispatcher,
		mode=SessionMode.SOCIAL,
		topic="topic",
		stop_condition=StopAfterRound(1),
	)

	events = [event async for event in session.stream()]
	feed = session._shared.artifacts["feed"]

	assert events[-1].type == MultiAgentEventType.DONE
	assert feed[0]["agent"] == "alpha"
	assert feed[0]["content"] == "json"
	assert feed[1] == {"type": "post", "agent": "beta", "content": "plain", "round": 0}


@pytest.mark.asyncio
async def test_multi_agent_session_prompt_includes_persona_history_and_feed():
	session = MultiAgentSession(
		[_agent("alpha"), _agent("beta")],
		ReplyDispatcher(),
		mode=SessionMode.SOCIAL,
		topic="topic",
		persona={"alpha": {"role": "analyst", "team": "blue"}},
	)
	session._shared.add_message("beta", "previous", 0)
	session._shared.artifacts["feed"] = [{"type": "post", "content": "feed"}]

	prompt = session._build_prompt(_agent("alpha"))

	assert "讨论主题：topic" in prompt
	assert "你的角色：analyst" in prompt
	assert "[beta] previous" in prompt
	assert "--- 信息流 ---" in prompt
	assert "请回应 [beta]" in prompt


@pytest.mark.asyncio
async def test_multi_agent_session_generates_string_persona_with_utility_model():
	class Utility:
		async def chat(self, messages):
			from axc_agent_engine.core.schema import LLMMessage, LLMResponse
			return LLMResponse(message=LLMMessage(content="角色：专家\n立场：中立\n背景：工程\n行为规则：简洁"))

	session = MultiAgentSession(
		[_agent("alpha")],
		ReplyDispatcher(),
		topic="topic",
		persona={"alpha": "expert"},
		utility_model=Utility(),
	)

	await session._ensure_persona_generated()

	assert session._persona["alpha"]["role"] == "专家"


@pytest.mark.asyncio
async def test_agent_orchestration_worker_raises_dispatcher_error_reply():
	worker = AgentOrchestrationWorker(ReplyDispatcher(fail={"child"}), "conv", "trace")

	with pytest.raises(RuntimeError, match="child failed"):
		await worker.request(_agent("child"), "prompt")


def test_multi_agent_event_sink_result_line_ignores_non_message_events():
	sink = MultiAgentEventSink()

	assert sink.result_line(sink.message("a", "content", 1)) == "[a] content"
	assert sink.result_line(sink.error("a", "bad", 1)) == "[错误] bad"
	assert sink.result_line(sink.done("done", 1)) == ""
