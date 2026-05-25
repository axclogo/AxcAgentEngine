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
