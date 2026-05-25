"""Tests for engine-level concurrency controls."""
import asyncio
import time

import pytest

from axc_agent_engine.agent import Agent
from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus, InMemoryCheckpointStore
from axc_agent_engine.runtime.concurrency import ConcurrencyConfig
from axc_agent_engine.core.context import ExecutionServices
from axc_agent_engine.engine import Engine
from axc_agent_engine.core.errors import ExecutionTimeoutError
from axc_agent_engine.llm.rate_limited import RateLimitedProvider
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMUsage, RuntimeConfig


class SlowProvider:
	def __init__(self, delay: float = 0.05) -> None:
		self.model = "slow"
		self.tool_name_mapping = None
		self.delay = delay
		self.active = 0
		self.max_active = 0
		self.calls: list[str] = []

	async def chat(self, messages, tools=None, **kwargs):
		self.active += 1
		self.max_active = max(self.max_active, self.active)
		try:
			await asyncio.sleep(self.delay)
			content = messages[-1]["content"]
			self.calls.append(content)
			return LLMResponse(
				message=LLMMessage(role="assistant", content=f"ok:{content}"),
				usage=LLMUsage(input_tokens=1, output_tokens=1),
			)
		finally:
			self.active -= 1

	async def stream(self, messages, tools=None, **kwargs):
		response = await self.chat(messages, tools, **kwargs)
		yield response.message.content

	async def ask(self, prompt, **kwargs):
		return (await self.chat([{"role": "user", "content": prompt}], **kwargs)).message.content

	async def close(self):
		pass


@pytest.mark.asyncio
async def test_same_session_is_serialized_by_default():
	provider = SlowProvider()
	agent = Agent("a", "", "", RuntimeConfig(), [], provider, None)

	start = time.monotonic()
	results = await asyncio.gather(agent.chat("one", session_id="s"), agent.chat("two", session_id="s"))
	elapsed = time.monotonic() - start

	assert results == ["ok:one", "ok:two"]
	assert provider.max_active == 1
	assert elapsed >= provider.delay * 2


@pytest.mark.asyncio
async def test_different_sessions_can_run_concurrently():
	provider = SlowProvider()
	agent = Agent("a", "", "", RuntimeConfig(), [], provider, None)

	start = time.monotonic()
	results = await asyncio.gather(agent.chat("one", session_id="s1"), agent.chat("two", session_id="s2"))
	elapsed = time.monotonic() - start

	assert sorted(results) == ["ok:one", "ok:two"]
	assert provider.max_active == 2
	assert elapsed < provider.delay * 1.8


@pytest.mark.asyncio
async def test_agent_concurrency_limit_serializes_different_sessions():
	provider = SlowProvider()
	runtime = RuntimeConfig(concurrency={"max_agent_concurrent_runs": 1})
	agent = Agent("a", "", "", runtime, [], provider, None)

	await asyncio.gather(agent.chat("one", session_id="s1"), agent.chat("two", session_id="s2"))

	assert provider.max_active == 1


@pytest.mark.asyncio
async def test_agent_concurrency_queue_timeout():
	provider = SlowProvider(delay=0.08)
	runtime = RuntimeConfig(concurrency={"max_agent_concurrent_runs": 1, "queue_timeout": 0.01})
	agent = Agent("a", "", "", runtime, [], provider, None)

	with pytest.raises(ExecutionTimeoutError):
		await asyncio.gather(agent.chat("one", session_id="s1"), agent.chat("two", session_id="s2"))


@pytest.mark.asyncio
async def test_engine_concurrency_limit_shared_across_agents(tmp_path):
	provider = SlowProvider()
	engine = Engine(default_llm=provider, concurrency=ConcurrencyConfig(max_engine_concurrent_runs=1))
	for name in ("a1", "a2"):
		(tmp_path / f"{name}.yaml").write_text(f"name: {name}\nsystem_prompt: test\n")
	a1 = engine.load_agent(str(tmp_path / "a1.yaml"))
	a2 = engine.load_agent(str(tmp_path / "a2.yaml"))

	await asyncio.gather(a1.chat("one", session_id="s1"), a2.chat("two", session_id="s2"))

	assert provider.max_active == 1


@pytest.mark.asyncio
async def test_rate_limited_provider_limits_concurrency():
	provider = SlowProvider()
	wrapped = RateLimitedProvider(provider, max_concurrent=1)

	await asyncio.gather(
		wrapped.chat([{"role": "user", "content": "one"}]),
		wrapped.chat([{"role": "user", "content": "two"}]),
	)

	assert provider.max_active == 1


@pytest.mark.asyncio
async def test_rate_limited_provider_rpm_queue_timeout():
	provider = SlowProvider(delay=0)
	wrapped = RateLimitedProvider(provider, requests_per_minute=1, queue_timeout=0.01)

	await wrapped.chat([{"role": "user", "content": "one"}])
	with pytest.raises(ExecutionTimeoutError):
		await wrapped.chat([{"role": "user", "content": "two"}])


@pytest.mark.asyncio
async def test_resume_stream_uses_session_gate():
	provider = SlowProvider()
	store = InMemoryCheckpointStore()
	for run_id in ("r1", "r2"):
		await store.save(Checkpoint(
			run_id=run_id,
			sequence=1,
			kind="round",
			status=CheckpointStatus.INTERRUPTED,
			state={
				"current_round": 1,
				"messages": [{"role": "user", "content": run_id}],
				"metadata": {"session_id": "s", "agent_name": "a"},
			},
		))
	agent = Agent(
		"a",
		"",
		"",
		RuntimeConfig(max_rounds=5),
		[],
		provider,
		None,
		services=ExecutionServices(checkpoint_store=store),
	)

	await asyncio.gather(agent.resume("r1", llm_options={"stream": False}), agent.resume("r2", llm_options={"stream": False}))

	assert provider.max_active == 1
