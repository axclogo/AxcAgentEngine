"""Tests for multi-agent mode profiles."""
from __future__ import annotations

import pytest

from axc_agent_engine.sidecar.multi_agent.modes import (
    MODE_PROFILES,
    ModeRuntime,
    build_scheduler_for_mode,
    build_stop_condition_for_mode,
    mode_prompt_guidance,
)
from axc_agent_engine.sidecar.multi_agent.types import SessionMode
from axc_agent_engine.sidecar.multi_agent.persona import build_persona_prompt, generate_persona
from axc_agent_engine.sidecar.multi_agent.scheduler.redblue import RedBlueScheduler
from axc_agent_engine.sidecar.multi_agent.scheduler.supervisor import SupervisorScheduler
from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext
from axc_agent_engine.sidecar.multi_agent.stop_condition.llm_based import LLMBasedStop
from axc_agent_engine.core.schema import LLMMessage, LLMResponse


class AgentStub:
    def __init__(self, name: str) -> None:
        self.name = name


def test_all_non_custom_modes_have_profiles():
    expected = set(SessionMode) - {SessionMode.CUSTOM}

    assert set(MODE_PROFILES) == expected


@pytest.mark.parametrize("mode", list(set(SessionMode) - {SessionMode.CUSTOM}))
def test_mode_profiles_build_scheduler_and_stop_condition(mode):
    agents = [AgentStub("a"), AgentStub("b")]
    supervisor = AgentStub("supervisor")
    runtime = ModeRuntime(agents=agents, supervisor=supervisor, max_rounds=2)

    scheduler = build_scheduler_for_mode(mode, runtime)
    stop_condition = build_stop_condition_for_mode(mode, runtime)

    assert scheduler is not None
    assert stop_condition is not None
    assert mode_prompt_guidance(mode)


def test_debate_requires_two_agents():
    runtime = ModeRuntime(agents=[AgentStub("solo")])

    with pytest.raises(ValueError, match="at least 2"):
        build_scheduler_for_mode(SessionMode.DEBATE, runtime)


def test_supervisor_requires_supervisor_agent():
    runtime = ModeRuntime(agents=[AgentStub("worker")])

    with pytest.raises(ValueError, match="supervisor"):
        build_scheduler_for_mode(SessionMode.SUPERVISOR, runtime)


def test_build_persona_prompt_and_parse_generated_persona():
    persona = {
        "role": "analyst",
        "stance": "skeptical",
        "background": "security",
        "rules": "be concise",
        "team": "blue",
    }
    prompt = build_persona_prompt("a", persona)
    assert "你的角色：analyst" in prompt
    assert "你的队伍：blue" in prompt


async def test_generate_persona_success_and_fallback():
    class LLM:
        async def chat(self, messages):
            return LLMResponse(message=LLMMessage(content="角色：专家\n立场：谨慎\n背景：工程\n行为规则：简洁"))

    result = await generate_persona("topic", "hint", LLM())
    assert result["role"] == "专家"
    assert result["stance"] == "谨慎"

    class BadLLM:
        async def chat(self, messages):
            return "bad"

    assert await generate_persona("topic", "fallback", BadLLM()) == {"role": "fallback"}


def test_supervisor_scheduler_parses_assign_and_fallback():
    supervisor = AgentStub("sup")
    workers = [AgentStub("alice"), AgentStub("bob")]
    scheduler = SupervisorScheduler(supervisor, workers)
    ctx = SharedContext()
    assert scheduler.steps_per_round([]) == 2
    assert scheduler.select_speakers(ctx, [], 0) == [supervisor]
    ctx.add_message("sup", "ASSIGN:alice: do it", 0)
    assert scheduler.select_speakers(ctx, [], 1)[0].name == "alice"
    ctx.messages[-1]["content"] = "bob should handle"
    assert scheduler.select_speakers(ctx, [], 1)[0].name == "bob"
    ctx.messages[-1]["content"] = "unknown"
    assert scheduler.select_speakers(ctx, [], 1)[0].name == "alice"
    assert scheduler.select_speakers(ctx, [], 1)[0].name == "bob"


def test_redblue_scheduler_splits_without_tags_and_handles_empty_team():
    agents = [AgentStub("a"), AgentStub("b"), AgentStub("c")]
    scheduler = RedBlueScheduler(agents)

    assert scheduler.steps_per_round(agents) == 2
    assert scheduler.select_speakers(SharedContext(), agents, 0)[0].name == "a"
    assert scheduler.select_speakers(SharedContext(), agents, 1)[0].name == "b"

    empty_blue = RedBlueScheduler([AgentStub("red")], {"red": {"team": "red"}})
    assert empty_blue.select_speakers(SharedContext(), [AgentStub("fallback")], 1)[0].name == "fallback"
    assert empty_blue.select_speakers(SharedContext(), [], 1) == []


def test_redblue_scheduler_uses_judge_interval_over_team_turns():
    red = AgentStub("red")
    blue = AgentStub("blue")
    judge = AgentStub("judge")
    scheduler = RedBlueScheduler(
        [red, blue, judge],
        {"red": {"team": "red"}, "blue": {"team": "blue"}, "judge": {"team": "judge"}},
        judge_interval=2,
    )

    assert scheduler.select_speakers(SharedContext(), [red, blue, judge], 2) == [judge]


class StopStub(LLMBasedStop):
    prompt_template = "content={content}"
    success_keyword = "DONE"
    success_reason = "finished"
    recent_limit = 2


async def test_llm_based_stop_max_round_and_interval_boundaries():
    ctx = SharedContext(topic="topic")
    stop = StopStub(llm_client=None, check_interval=3, max_rounds=2)

    assert await stop.should_stop(ctx, 2) == (True, "已达到最大轮次 2")
    assert await StopStub(llm_client=None, check_interval=3).should_stop(ctx, 1) == (False, "")


async def test_llm_based_stop_success_guidance_and_llm_failure():
    class LLM:
        def __init__(self, answer: str) -> None:
            self.answer = answer
            self.prompts: list[str] = []

        async def ask(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return self.answer

    ctx = SharedContext(topic="topic")
    ctx.add_message("a", "first", 0)
    llm = LLM("NO\ntry another route")

    assert await StopStub(llm_client=llm, check_interval=1).should_stop(ctx, 1) == (False, "")
    assert llm.prompts == ["content=[a] first"]
    assert ctx.messages[-1]["agent"] == "__system__"
    assert "try another route" in ctx.messages[-1]["content"]

    assert await StopStub(llm_client=LLM(" DONE "), check_interval=1).should_stop(ctx, 1) == (True, "finished")

    class FailingLLM:
        async def ask(self, prompt: str) -> str:
            raise RuntimeError("provider down")

    assert await StopStub(llm_client=FailingLLM(), check_interval=1).should_stop(ctx, 1) == (False, "")
