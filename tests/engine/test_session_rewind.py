"""Session rewind and fork history tests. 会话回退与分叉历史测试。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from axc_agent_engine import (
	AgentModels,
	Checkpoint,
	Engine,
	InMemoryCheckpointStore,
	PluginRegistry,
	fork_failure_of,
)
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMStreamChunk, LLMUsage, ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.config_schema import config_schema
from axc_agent_engine.plugins.strategies.react import ReActPlugin
from axc_agent_engine.storage.in_memory import InMemoryMessagePersistence
from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig
from axc_agent_engine.tools.tool_output import ToolOutput


class ScriptedProvider:
	model = "test"
	tool_name_mapping = ToolNameMappingConfig()

	def __init__(self, responses: list[LLMResponse]) -> None:
		self.responses = responses
		self.index = 0
		self.requests: list[list[dict]] = []

	async def chat(self, messages, tools=None, **kwargs):
		self.requests.append(deepcopy(messages))
		response = self.responses[min(self.index, len(self.responses) - 1)]
		self.index += 1
		return response

	async def stream(self, messages, tools=None, **kwargs):
		response = await self.chat(messages, tools, **kwargs)
		message = response.message
		if message.tool_calls:
			for index, tool_call in enumerate(message.tool_calls):
				yield LLMStreamChunk(tool_call_delta={**tool_call, "index": index})
			return
		if message.content:
			yield LLMStreamChunk(content_delta=message.content, usage=response.usage)

	async def ask(self, messages, **kwargs) -> str:
		return "ok"

	async def close(self) -> None:
		pass


class BlockingProvider(ScriptedProvider):
	def __init__(self, responses: list[LLMResponse]) -> None:
		super().__init__(responses)
		self.started = asyncio.Event()
		self.release = asyncio.Event()

	async def stream(self, messages, tools=None, **kwargs):
		self.started.set()
		await self.release.wait()
		async for chunk in super().stream(messages, tools, **kwargs):
			yield chunk


class EchoPlugin(BasePlugin):
	name = "echo"
	config_schema = config_schema("echo", "Echo", "Echo tool for tests.", [])

	def get_tools(self) -> list[ToolDefinition]:
		async def echo(args, context):
			return ToolOutput.text("tool-result")

		return [ToolDefinition(name="echo", description="echo", execute=echo, is_read_only=True)]


class RewindProbe(BasePlugin):
	name = "rewind_probe"
	config_schema = config_schema("rewind_probe", "Rewind probe", "Records rewind calls.", [])
	calls: ClassVar[list[tuple[str, str, str]]] = []

	async def on_session_rewind(self, agent_name: str, session_id: str, before_run_id: str) -> None:
		type(self).calls.append((agent_name, session_id, before_run_id))


class FlakyPersistence(InMemoryMessagePersistence):
	def __init__(self) -> None:
		super().__init__()
		self.fail = False

	async def save(self, scope_id: str, session_id: str, messages: list[dict]) -> None:
		if self.fail:
			raise OSError("persistence unavailable")
		await super().save(scope_id, session_id, messages)


class FailingSaveStore(InMemoryCheckpointStore):
	def __init__(self) -> None:
		super().__init__()
		self.fail_save = False

	async def save(self, checkpoint) -> None:
		if self.fail_save:
			raise OSError("checkpoint save failed")
		await super().save(checkpoint)


class RecoverableStore(InMemoryCheckpointStore):
	def __init__(self) -> None:
		super().__init__()
		self.fail_delete = False

	async def delete_run(self, run_id: str) -> None:
		if self.fail_delete:
			raise OSError("checkpoint delete failed")
		await super().delete_run(run_id)


class BlockingPersistence(InMemoryMessagePersistence):
	def __init__(self) -> None:
		super().__init__()
		self.block = False
		self.started = asyncio.Event()
		self.release = asyncio.Event()

	async def save(self, scope_id: str, session_id: str, messages: list[dict]) -> None:
		if self.block:
			self.started.set()
			await self.release.wait()
		await super().save(scope_id, session_id, messages)


def _response(content: str = "", tool_calls: list[dict] | None = None) -> LLMResponse:
	return LLMResponse(
		message=LLMMessage(content=content, tool_calls=tool_calls or []),
		usage=LLMUsage(input_tokens=1, output_tokens=1),
	)


def _echo_call() -> dict:
	return {"id": "tc-1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}


async def collect(events):
	return [event async for event in events]


@asynccontextmanager
async def host(
	tmp_path: Path,
	provider,
	persistence=None,
	store=None,
	extra: type[BasePlugin] | None = None,
	name: str = "agent",
):
	registry = PluginRegistry()
	plugins = [ReActPlugin, EchoPlugin, *([extra] if extra else [])]
	registry.register_many(plugins)
	config: dict = {"react": {"enabled": True}, "echo": {"enabled": True}}
	if extra:
		config[extra.name] = {"enabled": True}
	path = tmp_path / f"{name}.yaml"
	path.write_text(yaml.safe_dump({"name": name, "plugins": config}), encoding="utf-8")
	engine = Engine(
		plugin_registry=registry,
		message_persistence=persistence,
		checkpoint_store=store or InMemoryCheckpointStore(),
	)
	try:
		yield engine, engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=provider))
	finally:
		await engine.close()


async def test_fork_copies_session_history_and_survives_restart(tmp_path: Path) -> None:
	persistence = InMemoryMessagePersistence()
	store = InMemoryCheckpointStore()
	provider = ScriptedProvider(
		[_response(tool_calls=[_echo_call()]), _response("first-done"), _response("branch-done")]
	)
	async with host(tmp_path, provider, persistence, store) as (_, agent):
		events = await collect(agent.stream("first", session_id="s1", metadata={"run_id": "r1"}))
		assert events[-1].content == "first-done"
		await collect(agent.stream("parent-second", session_id="s1", metadata={"run_id": "r1b"}))
		cuts = [checkpoint for checkpoint in await store.list("r1") if checkpoint.forkable]
		cut = max(cuts, key=lambda checkpoint: checkpoint.sequence)
		parent_messages = deepcopy((await agent.get_session("s1")).messages)
		branch = await agent.fork_run("r1", cut.id)
		child = await agent.get_session(branch.session_id)
		assert child.messages == cut.state["messages"]
		assert any(message["role"] == "tool" for message in child.messages)
		assert "parent-second" not in str(child.messages)
		assert (await agent.get_session("s1")).messages == parent_messages
		await collect(agent.stream("branch", session_id=branch.session_id, metadata={"run_id": "r2"}))
		assert any(
			message.get("role") == "tool" and "tool-result" in str(message.get("content"))
			for message in provider.requests[-1]
		)
		branch_history = deepcopy((await agent.get_session(branch.session_id)).messages)
	assert branch_history
	restart_provider = ScriptedProvider([_response("restart-done")])
	async with host(tmp_path, restart_provider, persistence, store) as (_, agent):
		await collect(agent.stream("restart", session_id=branch.session_id, metadata={"run_id": "r3"}))
		assert any(
			message.get("role") == "tool" and "tool-result" in str(message.get("content"))
			for message in restart_provider.requests[-1]
		)


async def test_failed_fork_exposes_ids_and_discard_is_idempotent(tmp_path: Path) -> None:
	persistence = FlakyPersistence()
	store = RecoverableStore()
	async with host(tmp_path, ScriptedProvider([_response("done")]), persistence, store) as (_, agent):
		await collect(agent.stream("first", session_id="s1", metadata={"run_id": "r1"}))
		cut = next(checkpoint for checkpoint in await store.list("r1") if checkpoint.forkable)
		before = set(await store.list_runs())
		persistence.fail = True
		store.fail_delete = True
		with pytest.raises(OSError, match="persistence unavailable") as info:
			await agent.fork_run("r1", cut.id)
		failure = fork_failure_of(info.value)
		assert failure is not None
		assert failure.branch_run_id in set(await store.list_runs())
		assert failure.branch_session_id
		assert failure.cause.startswith("OSError:")
		assert "checkpoint" in failure.rollback_error
		assert failure.cleaned is False
		assert failure.checkpoint_written is True
		assert failure.session_written is None
		store.fail_delete = False
		first = await agent.discard_fork(failure)
		assert first.cleaned is True
		assert first.checkpoint_removed is True
		assert first.session_removed is True
		assert set(await store.list_runs()) == before
		assert await agent.get_session(failure.branch_session_id) is None
		second = await agent.discard_fork(failure)
		assert second.cleaned is True
		assert set(await store.list_runs()) == before


async def test_fork_rolls_back_checkpoint_when_session_write_fails(tmp_path: Path) -> None:
	persistence = FlakyPersistence()
	store = InMemoryCheckpointStore()
	provider = ScriptedProvider([_response("done")])
	async with host(tmp_path, provider, persistence, store) as (_, agent):
		await collect(agent.stream("first", session_id="s1", metadata={"run_id": "r1"}))
		cut = next(checkpoint for checkpoint in await store.list("r1") if checkpoint.forkable)
		before = set(await store.list_runs())
		persistence.fail = True
		with pytest.raises(OSError, match="persistence unavailable"):
			await agent.fork_run("r1", cut.id)
		assert set(await store.list_runs()) == before


async def test_rewind_restores_history_and_survives_restart(tmp_path: Path) -> None:
	persistence = InMemoryMessagePersistence()
	store = InMemoryCheckpointStore()
	RewindProbe.calls.clear()
	provider = ScriptedProvider(
		[
			_response("one"),
			_response(tool_calls=[_echo_call()]),
			_response("two"),
			_response("three"),
			_response("after-rewind"),
			_response("restart"),
		]
	)
	async with host(tmp_path, provider, persistence, store, RewindProbe) as (_, agent):
		await collect(agent.stream("first", session_id="s1", metadata={"run_id": "r1"}))
		one_history = deepcopy((await agent.get_session("s1")).messages)
		await collect(agent.stream("second", session_id="s1", metadata={"run_id": "r2"}))
		await collect(agent.stream("third", session_id="s1", metadata={"run_id": "r3"}))
		assert await agent.can_rewind("s1", "r2")
		result = await agent.rewind_session("s1", before_run_id="r2")
		assert result.kept_messages == len(one_history)
		assert (await agent.get_session("s1")).messages == one_history
		assert RewindProbe.calls == [("agent", "s1", "r2")]
		empty = await agent.rewind_session("s1", before_run_id="r1")
		assert empty.kept_messages == 0
		assert (await agent.get_session("s1")).messages == []
		await collect(agent.stream("fresh", session_id="s1", metadata={"run_id": "r4"}))
		assert "two" not in str(provider.requests[-1])
		assert "three" not in str(provider.requests[-1])
		assert not await agent.can_rewind("s1", "missing")
		with pytest.raises(ValueError, match="No rewind record"):
			await agent.rewind_session("s1", before_run_id="missing")
	restart_provider = ScriptedProvider([_response("restart")])
	async with host(tmp_path, restart_provider, persistence, store) as (_, agent):
		assert await agent.can_rewind("s1", "r1")
		await collect(agent.stream("restart", session_id="s1", metadata={"run_id": "r5"}))
		assert "two" not in str(restart_provider.requests[-1])
		assert "three" not in str(restart_provider.requests[-1])


async def test_rewind_rejects_active_run(tmp_path: Path) -> None:
	provider = BlockingProvider([_response("blocked")])
	store = InMemoryCheckpointStore()
	provider.release.set()
	async with host(tmp_path, provider, None, store) as (_, agent):
		await collect(agent.stream("first", session_id="s1", metadata={"run_id": "r1"}))
		provider.started.clear()
		provider.release.clear()
		task = asyncio.create_task(collect(agent.stream("active", session_id="s1", metadata={"run_id": "r2"})))
		await provider.started.wait()
		with pytest.raises(RuntimeError, match="active or queued"):
			await agent.rewind_session("s1", before_run_id="r1")
		provider.release.set()
		await task


async def test_ownerless_checkpoint_requires_identityless_caller(tmp_path: Path) -> None:
	store = InMemoryCheckpointStore()
	async with host(tmp_path, ScriptedProvider([_response("one"), _response("two")]), None, store) as (_, agent):
		await collect(agent.stream("first", session_id="s1", metadata={"run_id": "r1"}))
		cut = next(checkpoint for checkpoint in await store.list("r1") if checkpoint.forkable)
		branch = await agent.fork_run("r1", cut.id)
		with pytest.raises(ValueError, match="no owner record"):
			await agent.fork_run("r1", cut.id, metadata={"user_id": "u1"})
		with pytest.raises(ValueError, match="no owner record"):
			await agent.resume(branch.run_id, metadata={"user_id": "u1"})
		with pytest.raises(ValueError, match="no owner record"):
			await agent.resume("r1", metadata={"user_id": "u1"})
		assert await agent.resume("r1") == "one"
		await collect(agent.stream("second", session_id="s2", metadata={"run_id": "r2", "user_id": "u1"}))
		owned = next(checkpoint for checkpoint in await store.list("r2") if checkpoint.forkable)
		with pytest.raises(ValueError, match="another tenant or user"):
			await agent.fork_run("r2", owned.id, metadata={"user_id": "u2"})
		await agent.fork_run("r2", owned.id, metadata={"user_id": "u1"})


async def test_rewind_drops_live_input_from_target_run(tmp_path: Path) -> None:
	provider = BlockingProvider([_response("one"), _response("two"), _response("live-reply")])
	provider.release.set()
	store = InMemoryCheckpointStore()
	async with host(tmp_path, provider, None, store) as (_, agent):
		await collect(agent.stream("first", session_id="s1", metadata={"run_id": "r1"}))
		one_history = deepcopy((await agent.get_session("s1")).messages)
		provider.started.clear()
		provider.release.clear()
		task = asyncio.create_task(collect(agent.stream("second", session_id="s1", metadata={"run_id": "r2"})))
		await provider.started.wait()
		assert await agent.send_message("r2", "live", session_id="s1", message_id="m1") == "accepted"
		provider.release.set()
		await task
		assert "live" in str((await agent.get_session("s1")).messages)
		result = await agent.rewind_session("s1", before_run_id="r2")
		assert result.kept_messages == len(one_history)
		history = (await agent.get_session("s1")).messages
		assert history == one_history
		assert "live" not in str(history)


async def test_fork_cancellation_leaves_no_branch_history_or_checkpoint(tmp_path: Path) -> None:
	persistence = BlockingPersistence()
	store = InMemoryCheckpointStore()
	async with host(tmp_path, ScriptedProvider([_response("done")]), persistence, store) as (_, agent):
		await collect(agent.stream("first", session_id="s1", metadata={"run_id": "r1"}))
		cut = next(checkpoint for checkpoint in await store.list("r1") if checkpoint.forkable)
		before = set(await store.list_runs())
		persistence.block = True
		task = asyncio.create_task(agent.fork_run("r1", cut.id))
		await persistence.started.wait()
		task.cancel()
		with pytest.raises(asyncio.CancelledError) as info:
			await task
		failure = fork_failure_of(info.value)
		assert failure is not None
		assert failure.branch_run_id
		assert failure.branch_session_id
		assert set(await store.list_runs()) == before


@pytest.mark.parametrize(
	"history",
	[
		[{"role": "assistant", "tool_calls": [{}]}],
		[{"role": "assistant", "tool_calls": {}}],
		[{"role": "assistant", "tool_calls": False}],
		[{"role": "assistant", "tool_calls": 0}],
		[{"role": "assistant", "tool_calls": 7}],
		[{"role": "tool", "tool_call_id": []}],
		[{"role": "tool", "tool_call_id": {}}],
		[{"role": "tool", "tool_call_id": 1}],
	],
)
async def test_can_rewind_returns_false_for_malformed_history(tmp_path: Path, history: list[dict]) -> None:
	store = InMemoryCheckpointStore()
	async with host(tmp_path, ScriptedProvider([_response("one")]), None, store) as (_, agent):
		await store.save(
			Checkpoint(
				run_id="bad",
				state={
					"phase": "start",
					"metadata": {"session_id": "s1", "agent_name": "agent"},
					"history_start": history,
				},
			)
		)
		assert not await agent.can_rewind("s1", "bad")
		with pytest.raises(ValueError, match="Invalid checkpoint"):
			await agent.rewind_session("s1", before_run_id="bad")


async def test_fork_checkpoint_failure_has_structured_context(tmp_path: Path) -> None:
	store = FailingSaveStore()
	async with host(tmp_path, ScriptedProvider([_response("done")]), None, store) as (_, agent):
		await collect(agent.stream("first", session_id="s1", metadata={"run_id": "r1"}))
		cut = next(checkpoint for checkpoint in await store.list("r1") if checkpoint.forkable)
		before = set(await store.list_runs())
		store.fail_save = True
		with pytest.raises(OSError, match="checkpoint save failed") as info:
			await agent.fork_run("r1", cut.id)
		failure = fork_failure_of(info.value)
		assert failure is not None
		assert failure.checkpoint_written is None
		assert failure.session_written is False
		assert failure.cleaned is True
		assert set(await store.list_runs()) == before


async def test_discard_fork_validates_and_reports_storage_errors(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	persistence = FlakyPersistence()
	store = RecoverableStore()
	async with host(tmp_path, ScriptedProvider([_response("done")]), persistence, store) as (_, agent):
		await collect(agent.stream("first", session_id="s1", metadata={"run_id": "r1"}))
		cut = next(checkpoint for checkpoint in await store.list("r1") if checkpoint.forkable)
		persistence.fail = True
		store.fail_delete = True
		with pytest.raises(OSError) as info:
			await agent.fork_run("r1", cut.id)
		failure = fork_failure_of(info.value)
		assert failure is not None
		with pytest.raises(TypeError, match="ForkFailure"):
			await agent.discard_fork("bad")
		result = await agent.discard_fork(failure)
		assert result.cleaned is False
		assert result.checkpoint_removed is False
		assert any("checkpoint" in error for error in result.errors)

		async def fail_remove(session_id: str) -> None:
			raise OSError("session remove failed")

		monkeypatch.setattr(agent._session_manager, "remove", fail_remove)
		store.fail_delete = False
		result = await agent.discard_fork(failure)
		assert result.cleaned is False
		assert result.checkpoint_removed is True
		assert result.session_removed is False
		assert any("session" in error for error in result.errors)
