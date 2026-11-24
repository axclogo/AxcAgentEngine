"""POR runner — Plan-Observe-Replan 执行子流程。

执行 LLM 创建的多步骤计划。每个步骤运行一个子 ReAct 循环。
并行步骤使用隔离的子 MessageStore/ExecutionContext，避免竞态。
串行步骤共享主 MessageStore，保证上下文连续。
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace

from axc_agent_engine.core.errors import LLMError
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.react_loop import ReActTurnResult, por_visible_event
from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.planning.checkpointing import plan_from_state, plan_to_state, save_plan_checkpoint
from axc_agent_engine.planning.graph_runtime import PORGraphRuntime
from axc_agent_engine.planning.graph_state import PORGraphResult, PORGraphState
from axc_agent_engine.planning.observer import observe_step
from axc_agent_engine.planning.planner import Plan, PlanStep
from axc_agent_engine.planning.planning_service import PlanningService
from axc_agent_engine.planning.replanner import replan, should_replan
from axc_agent_engine.planning.runtime import PlanRuntime
from axc_agent_engine.planning.scheduler import get_next_steps, get_remaining_count, mark_step_done, mark_step_failed
from axc_agent_engine.planning.step_executor import build_step_prompt
from axc_agent_engine.runtime.checkpoint import CheckpointStatus
from axc_agent_engine.runtime.tasks import managed_stream

_MAX_STEP_ROUNDS_RATIO = 4
_MIN_STEP_ROUNDS = 5


@dataclass
class StepExecutionResult:
	"""POR step execution output before it is reduced into the parent context. 合并到父上下文之前的步骤执行结果。"""

	step_id: int
	result: str
	events: list[Event] = field(default_factory=list)
	input_tokens: int = 0
	output_tokens: int = 0
	isolated: bool = False


class StepRunner:
	def __init__(self, runtime: PlanRuntime) -> None:
		self._llm = runtime.llm_caller
		self._messages = runtime.message_store
		self._registry = runtime.registry
		self._pm = runtime.plugin_manager
		self._ctx = runtime.ctx
		self._step_factory = runtime.step_factory
		self._shared_kernel = self._step_factory(
			llm_caller=self._llm,
			registry=self._registry,
			plugin_manager=self._pm,
			ctx=self._ctx,
			messages=self._messages,
		)

	async def run_shared(self, plan: Plan, step: PlanStep) -> tuple[str, list[Event]]:
		step.status = StepStatus.RUNNING
		self._messages.append({"role": "user", "content": build_step_prompt(plan, step)})
		configured_max = max(_MIN_STEP_ROUNDS, self._ctx.config.max_rounds // _MAX_STEP_ROUNDS_RATIO)
		remaining_total = self._ctx.config.max_rounds - self._ctx.state.current_round
		max_step_rounds = min(configured_max, remaining_total)
		collected_events: list[Event] = []
		if self._ctx.state.current_round >= self._ctx.config.max_rounds:
			step.status = StepStatus.FAILED
			return "步骤超过总轮次限制", collected_events
		try:
			turn_result: ReActTurnResult
			async for item in self._shared_kernel.run_step(
				max_rounds=max_step_rounds,
				step_timeout=self._ctx.config.step_timeout,
				emit_tool_events=False,
				event_filter=por_visible_event,
			):
				if isinstance(item, ReActTurnResult):
					turn_result = item
				else:
					collected_events.append(item)
		except (LLMError, OSError, RuntimeError, ValueError) as e:
			step.status = StepStatus.FAILED
			return f"LLM 调用失败：{e}", collected_events
		content = turn_result.content
		if turn_result.failed:
			step.status = StepStatus.FAILED
			return content, collected_events
		step.status = StepStatus.DONE
		return content, collected_events

	async def run_isolated(self, plan: Plan, step: PlanStep) -> StepExecutionResult:
		child_messages, child_ctx = self._isolated_context(plan, step)
		child_kernel = self._step_factory(
			llm_caller=self._llm,
			registry=self._registry,
			plugin_manager=self._pm,
			ctx=child_ctx,
			messages=child_messages,
		)
		step.status = StepStatus.RUNNING
		configured_max = max(_MIN_STEP_ROUNDS, self._ctx.config.max_rounds // _MAX_STEP_ROUNDS_RATIO)
		collected_events: list[Event] = []
		try:
			turn_result: ReActTurnResult
			async for item in child_kernel.run_step(
				max_rounds=configured_max,
				step_timeout=self._ctx.config.step_timeout,
				emit_tool_events=False,
				event_filter=por_visible_event,
				round_budget=self._ctx.state,
			):
				if isinstance(item, ReActTurnResult):
					turn_result = item
				else:
					collected_events.append(item)
		except (LLMError, OSError, RuntimeError, ValueError) as e:
			return self._isolated_result(step, f"LLM 调用失败：{e}", collected_events, child_ctx, failed=True)
		return self._isolated_result(step, turn_result.content, collected_events, child_ctx, failed=turn_result.failed)

	def _isolated_result(
		self, step: PlanStep, result: str, events: list[Event], child_ctx, failed: bool
	) -> StepExecutionResult:
		step.status = StepStatus.FAILED if failed else StepStatus.DONE
		return StepExecutionResult(
			step_id=step.step_id,
			result=result,
			events=events,
			input_tokens=child_ctx.state.total_input_tokens,
			output_tokens=child_ctx.state.total_output_tokens,
			isolated=True,
		)

	def merge(self, step_result: StepExecutionResult) -> None:
		if step_result.input_tokens or step_result.output_tokens:
			self._ctx.add_usage(step_result.input_tokens, step_result.output_tokens)
		if step_result.isolated:
			self._messages.append(
				{
					"role": "assistant",
					"content": f"[POR 步骤 {step_result.step_id} 结果]\n{step_result.result}",
				}
			)

	def _isolated_context(self, plan: Plan, step: PlanStep):
		from axc_agent_engine.core.message_store import MessageStore as ChildMessageStore

		child_messages = ChildMessageStore()
		child_messages.init_system_prompt(self._ctx.config.system_prompt)
		child_messages.append({"role": "user", "content": build_step_prompt(plan, step)})
		return child_messages, self._ctx.fork_for_child({"por_step_id": step.step_id})


class PORExecutionService:
	"""POR behavior service used by the pydantic-graph runtime. 供 POR 图节点调用的执行服务。"""

	def __init__(self, runtime: PlanRuntime) -> None:
		self._llm = runtime.llm_caller
		self._messages = runtime.message_store
		self._registry = runtime.registry
		self._pm = runtime.plugin_manager
		self._ctx = runtime.ctx
		self._plan: Plan
		self._step_runner = StepRunner(runtime)
		self._checkpoint = runtime.checkpoint
		self._max_parallel_steps = runtime.max_parallel_steps
		self._unmerged = 0

	async def announce_plan(self, state: PORGraphState) -> None:
		self._plan = state.plan
		if not self._plan.steps:
			return
		if state.resume_state is not None:
			await self._restore_phase(state, state.resume_state)
			return
		self._messages.append(
			{
				"role": "assistant",
				"content": f"计划已创建：{self._plan.goal}（{len(self._plan.steps)} 个步骤）",
			}
		)
		state.emit(
			Event(
				type=EventType.PLAN_CREATED,
				content=self._plan.goal,
				steps=[{"step_id": s.step_id, "description": s.description} for s in self._plan.steps],
			)
		)
		await self._pm.on_plan_created(
			self._ctx,
			{
				"goal": self._plan.goal,
				"steps": [{"step_id": s.step_id, "description": s.description} for s in self._plan.steps],
			},
		)
		await self._save_plan_checkpoint(CheckpointStatus.RUNNING, phase="plan_created")

	async def _restore_phase(self, state: PORGraphState, snapshot: dict) -> None:
		phase = snapshot.get("phase")
		metadata = snapshot.get("metadata", {})
		failed_id = metadata.get("replan_step_id")
		if failed_id is None and phase not in {"step_failed", "finalize", "goal_done"}:
			return
		if await self._apply_input(state):
			return
		if phase == "step_failed" or failed_id is not None:
			cursor = snapshot.get("cursor", {}).get("current_step_id", snapshot.get("current_step_id"))
			step_id = _checkpoint_step_id(failed_id if failed_id is not None else cursor)
			if step_id is None or not any(
				step.step_id == step_id and step.status == StepStatus.FAILED for step in state.plan.steps
			):
				state.error = "Failed POR checkpoint requires its failed step ID"
				return
			if should_replan(state.plan):
				await self._do_replan(step_id)
				state.plan = self._plan
		elif phase == "finalize":
			summary = metadata.get("summary")
			if summary is None:
				last = self._messages.get_recent(1)
				if last and last[0].get("role") == "assistant" and not last[0].get("tool_calls"):
					summary = last[0].get("content")
			if not isinstance(summary, str):
				state.error = "Finalized POR checkpoint is missing its saved summary"
				return
			state.emit(Event.done(summary))
			state.finalized = True
		elif metadata.get("goal_achieved") or all(s.status == StepStatus.DONE for s in state.plan.steps):
			async for event in self._finalize_plan(state.user_message):
				state.emit(event)
			state.finalized = True

	async def _apply_input(self, state: PORGraphState) -> bool:
		inbox = self._ctx.runtime.inbox
		if inbox is None or not (pending := await inbox.peek()):
			return False
		plan = state.plan
		completed = [step for step in plan.steps if step.status == StepStatus.DONE]
		history = "\n".join(f"{step.description}: {step.result}" for step in completed)
		remaining = "\n".join(step.description for step in plan.steps if step.status == StepStatus.PENDING)
		request = "\n".join(pending)
		goal = f"原目标：{plan.goal}\n已完成（请勿重复）：{history}\n待执行：{remaining}\n用户补充：{request}"
		revised = await PlanningService.generate_plan(self._llm, self._ctx, goal)
		offset = max(step.step_id for step in plan.steps)
		for step in revised.steps:
			step.step_id += offset
			step.depends_on = [dep + offset for dep in step.depends_on]
		revised.steps = completed + revised.steps
		await inbox.take(
			count=len(pending), progress={"strategy_state": plan_to_state(revised, phase="revised"), "forkable": True}
		)
		state.plan = self._plan = revised
		state.user_message = request
		state.goal_achieved = False
		state.emit(
			Event(
				type=EventType.PLAN_CREATED,
				content=revised.goal,
				metadata={"revised": True},
				steps=[{"step_id": step.step_id, "description": step.description} for step in revised.steps],
			)
		)
		return True

	async def select_steps(self, state: PORGraphState) -> None:
		state.should_continue = False
		if state.error or state.finalized:
			return
		if not state.plan.steps:
			state.error = "计划创建后步骤列表为空"
			return
		self._plan = state.plan
		state.next_steps = []
		self._ctx.check_cancelled()
		while await self._apply_input(state):
			self._ctx.check_cancelled()
		steps = get_next_steps(self._plan)
		remaining = self._ctx.config.max_rounds - self._ctx.state.current_round
		if steps and remaining <= 0:
			state.error = "步骤超过总轮次限制"
			return
		if steps:
			state.next_steps = steps[: min(remaining, self._max_parallel_steps)]
			state.current_step = state.next_steps[0] if len(state.next_steps) == 1 else None
			return
		unfinished = [step.step_id for step in state.plan.steps if step.status != StepStatus.DONE]
		if unfinished:
			state.error = f"计划没有可执行步骤，未完成的步骤：{unfinished}"
			return
		async for event in self._finalize_plan(state.user_message):
			state.emit(event)
		state.finalized = True

	async def execute_step(self, state: PORGraphState) -> None:
		if state.error or state.finalized:
			return
		single = len(state.next_steps) == 1
		if single:
			step = state.next_steps[0]
			await self._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=step.step_id, phase="step_start")
		inbox = self._ctx.runtime.inbox
		if inbox is not None and await inbox.peek():
			state.next_steps = []
			state.current_step = None
			return
		if single:
			step = state.next_steps[0]
			state.current_step = step
			state.step_result = ""
			start_event = Event(type=EventType.STEP_START, step_id=step.step_id, content=step.description)
			state.emit(start_event)
			self._ctx.state.metadata["por_step_id"] = step.step_id
			try:
				step_result, step_events = await self._execute_single_step(step)
			finally:
				self._ctx.state.metadata.pop("por_step_id", None)
			state.step_result = step_result
			for event in step_events:
				state.emit(event)
			return
		await self._execute_parallel(state)
		state.current_step = None

	async def observe_step(self, state: PORGraphState) -> None:
		if state.error or state.finalized or not state.current_step:
			return
		event, state.goal_achieved = await self._handle_step_result(state.current_step, state.step_result)
		await self._save_step_progress(state.current_step, state.goal_achieved)
		state.emit(event)

	async def replan_step(self, state: PORGraphState) -> None:
		state.should_continue = False
		if state.error or state.finalized:
			return
		if await self._apply_input(state):
			state.should_continue = True
			return
		if state.goal_achieved:
			async for event in self._finalize_plan(state.user_message):
				state.emit(event)
			state.finalized = True
			return
		if any(step.status == StepStatus.FAILED for step in state.next_steps) and should_replan(self._plan):
			failed_id = next(step.step_id for step in state.next_steps if step.status == StepStatus.FAILED)
			await self._do_replan(failed_id)
			state.plan = self._plan
		state.should_continue = True

	async def finalize_plan(self, state: PORGraphState) -> PORGraphResult:
		return PORGraphResult(final_content=state.final_content, error=state.error)

	async def _execute_parallel(self, state: PORGraphState) -> None:
		"""Run ready steps in isolated contexts.
		使用隔离 runtime 并行执行多个就绪步骤。

		                每个步骤拥有自己的子 MessageStore 和 ExecutionContext，避免消息顺序竞态
		                以及轮次计数冲突。"""
		steps = state.next_steps
		for step in steps:
			state.emit(Event(type=EventType.STEP_START, step_id=step.step_id, content=step.description))

		async def execute(step: PlanStep):
			local = replace(step)
			try:
				output = await self._execute_step_isolated(local)
			except (LLMError, OSError, RuntimeError, ValueError) as exc:
				local.status = StepStatus.FAILED
				output = StepExecutionResult(step_id=step.step_id, result=f"Step execution error: {exc}", isolated=True)
			return step, local.status, output

		goal_done = False
		failed_id = None
		tasks = [asyncio.create_task(execute(step)) for step in steps]
		try:
			self._unmerged = len(tasks)
			for completed in asyncio.as_completed(tasks):
				step, status, output = await completed
				step.status = status
				for event in output.events:
					state.emit(event)
				self._step_runner.merge(output)
				event, achieved = await self._handle_step_result(step, output.result)
				goal_done = goal_done or achieved
				if failed_id is None and step.status == StepStatus.FAILED:
					failed_id = step.step_id
				state.goal_achieved = goal_done and failed_id is None and not get_remaining_count(state.plan)
				self._unmerged -= 1
				await self._save_step_progress(step, state.goal_achieved, failed_id)
				state.emit(event)
		finally:
			for task in tasks:
				task.cancel()
			await asyncio.gather(*tasks, return_exceptions=True)

	async def _execute_step_isolated(self, step: PlanStep) -> StepExecutionResult:
		"""Execute a step with its own history and context.
		使用隔离的 MessageStore 和 ExecutionContext 执行单个步骤。"""
		return await self._step_runner.run_isolated(self._plan, step)

	async def _handle_step_result(self, step: PlanStep, step_result: str) -> tuple[Event, bool]:
		"""Observe a result and update the plan.
		处理步骤结果：observe → mark → replan，返回 (event, goal_achieved)。"""
		remaining = get_remaining_count(self._plan)
		observation = await observe_step(
			step.step_id,
			step.status,
			step_result,
			step.description,
			self._plan.goal,
			remaining,
			self._ctx.utility_model,
		)
		failed = step.status == StepStatus.FAILED or not observation.step_ok or observation.action == "replan"
		goal_done = not failed and observation.action == "done"
		if failed:
			mark_step_failed(self._plan, step.step_id, step_result)
		else:
			mark_step_done(self._plan, step.step_id, step_result)
		await self._pm.on_step_completed(
			self._ctx, {"step_id": step.step_id, "status": step.status.value, "result": step_result}
		)
		return Event(type=EventType.STEP_COMPLETED, step_id=step.step_id, content=step_result), goal_done

	async def _save_step_progress(self, step: PlanStep, goal_done: bool, failed_id: int | None = None) -> None:
		phase = "step_failed" if step.status == StepStatus.FAILED else "goal_done" if goal_done else "step_done"
		metadata: dict = {"goal_achieved": True} if goal_done else {}
		if failed_id is not None:
			metadata["replan_step_id"] = failed_id
		status = CheckpointStatus.COMPLETED if goal_done else CheckpointStatus.RUNNING
		await self._save_plan_checkpoint(status, step.step_id, phase, metadata)

	async def _do_replan(self, failed_step_id: int) -> None:
		"""Revise the remaining plan. 执行重规划。"""
		self._plan = await replan(self._plan, failed_step_id, self._ctx.utility_model)
		await self._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=failed_step_id, phase="replan")

	async def _execute_single_step(self, step: PlanStep) -> tuple[str, list[Event]]:
		"""Run the bounded model loop for one step.
		执行单个步骤（子 ReAct 循环），返回 (result, intermediate events)。

		                设计：步骤消息有意累积在共享 MessageStore 中。后续步骤需要前置步骤上下文
		                （工具结果、中间推理）来做决策，因此不要隔离串行步骤消息。"""
		return await self._step_runner.run_shared(self._plan, step)

	async def _finalize_plan(self, user_message: str) -> AsyncIterator[Event]:
		"""Summarize a completed plan. 计划完成后生成最终摘要。"""
		completed = [s for s in self._plan.steps if s.status == StepStatus.DONE]
		summary_parts = [f"目标：{self._plan.goal}", f"已完成 {len(completed)}/{len(self._plan.steps)} 个步骤"]
		for s in completed:
			summary_parts.append(f"- 步骤 {s.step_id}：{s.result}")
		history = self._messages.get_all()
		history.append({"role": "user", "content": "请根据计划执行结果给出最终摘要。\n" + "\n".join(summary_parts)})
		messages = self._pm.transform_messages(history, self._ctx, "")
		message, _ = await self._llm.call(self._ctx, messages, None)
		content = message.get("content", "") or "\n".join(summary_parts)
		self._messages.append({"role": "assistant", "content": content})
		await self._pm.on_round_end(self._ctx, user_message, content, [])
		await self._save_plan_checkpoint(CheckpointStatus.COMPLETED, phase="finalize", metadata={"summary": content})
		yield Event(type=EventType.STREAM_END, content=content)
		yield Event(type=EventType.DONE, content=content)

	async def _save_plan_checkpoint(
		self,
		status: CheckpointStatus,
		current_step_id: int | None = None,
		phase: str = "por",
		metadata: dict | None = None,
	) -> None:
		if self._checkpoint:
			state = plan_to_state(self._plan, current_step_id, phase)
			state["metadata"] = metadata or {}
			fields: dict = {
				"phase": phase,
				"strategy_state": state,
				"forkable": not self._unmerged
				and phase
				in {
					"plan_created",
					"replan",
					"step_done",
					"step_failed",
					"goal_done",
					"finalize",
				},
			}
			if self._unmerged:
				fields["fork_blocker"] = (
					f"Checkpoint is not a forkable cut: {self._unmerged} POR results not yet merged"
				)
			if phase in {"step_done", "goal_done"}:
				fields["settled_step"] = current_step_id
			await self._checkpoint("por", status, fields)
			return
		run_id = self._ctx.state.metadata.get("run_id", "")
		store = self._ctx.services.checkpoint_store
		if store is None or not run_id:
			return
		last = await store.latest(run_id)
		sequence = last.sequence + 1 if last else 1
		await save_plan_checkpoint(
			store,
			run_id,
			sequence,
			self._plan,
			status=status,
			current_step_id=current_step_id,
			metadata={"phase": phase, **(metadata or {})},
		)


class PORRunner:
	"""POR plan runner backed by pydantic-graph. 由 pydantic-graph 驱动的 POR 执行器。"""

	def __init__(self, runtime: PlanRuntime) -> None:
		self._service = PORExecutionService(runtime)
		self._graph_runtime = PORGraphRuntime(self._service)

	def run(self, plan: Plan, user_message: str) -> AsyncIterator[Event]:
		"""Execute a prepared plan.
		使用预先构建的 Plan 执行 POR 流程。"""
		return self._graph_runtime.run(plan, user_message)

	async def run_from_checkpoint_state(
		self,
		checkpoint_state: dict,
		user_message: str,
		run_id: str = "",
	) -> AsyncIterator[Event]:
		plan = plan_from_state(checkpoint_state)
		if not plan:
			yield Event.error(f"No POR checkpoint found for run_id={run_id}")
			return
		current_step_id = _checkpoint_step_id(
			checkpoint_state.get("cursor", {}).get("current_step_id", checkpoint_state.get("current_step_id"))
		)
		for step in plan.steps:
			if step.status == StepStatus.RUNNING:
				step.status = StepStatus.PENDING
		initial_event = Event(
			type=EventType.PLAN_CREATED,
			content=plan.goal,
			steps=[{"step_id": s.step_id, "description": s.description, "status": s.status.value} for s in plan.steps],
			metadata={
				"resumed": True,
				"phase": str(checkpoint_state.get("phase") or "por"),
				"current_step_id": current_step_id,
			},
		)
		async with managed_stream(
			self._graph_runtime.run(
				plan,
				user_message,
				initial_events=[initial_event],
				resume_state=checkpoint_state,
			)
		) as events:
			async for event in events:
				yield event


def _checkpoint_step_id(value) -> int | None:
	if value is None:
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return None
