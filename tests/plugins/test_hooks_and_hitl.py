import asyncio

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.plugins.builtin.hooks.plugin import HooksPlugin, _safe_eval_condition
from axc_agent_engine.plugins.builtin.human_in_the_loop.plugin import HumanInTheLoopPlugin, _risk_level
from axc_agent_engine.tools.tool_output import ToolOutput


async def test_hooks_pre_tool_transform_reject_and_llm_filters():
	plugin = HooksPlugin()
	plugin.initialize({"rules": [
		{"event": "pre_tool_call", "condition": "tool_name == 'write'", "action": "transform", "params": {"set": {"safe": True}}},
		{"event": "pre_tool_call", "condition": "arguments.get('block') == True", "action": "reject", "params": {"message": "no"}},
		{"event": "pre_llm_call", "action": "inject", "params": {"content": "ctx"}},
		{"event": "pre_llm_call", "action": "filter_tools", "params": {"allowed": ["keep"]}},
	]}, None)
	allowed, args = await plugin.pre_tool_call(ExecutionContext(), "write", {"x": 1})
	assert allowed is True
	assert args["safe"] is True
	allowed, _ = await plugin.pre_tool_call(ExecutionContext(), "read", {"block": True})
	assert allowed is False
	messages, tools = plugin.pre_llm_call(
		ExecutionContext(),
		[{"role": "user", "content": "hi"}],
		[{"function": {"name": "keep"}}, {"function": {"name": "drop"}}],
	)
	assert messages[-1]["content"] == "ctx"
	assert len(tools) == 1


async def test_hooks_notify_error_plan_step_and_post_tool():
	payloads = []
	plugin = HooksPlugin()
	plugin.initialize({"rules": [
		{"event": "on_error", "action": "notify", "params": {"callback": payloads.append}},
		{"event": "on_plan_created", "action": "notify", "params": {"callback": payloads.append}},
		{"event": "on_step_completed", "action": "notify", "params": {"callback": payloads.append}},
		{"event": "post_tool_call", "condition": "result.startswith('ok')", "action": "log"},
	]}, None)
	await plugin.on_error(ExecutionContext(), RuntimeError("boom"))
	await plugin.on_plan_created(ExecutionContext(), {"goal": "g"})
	await plugin.on_step_completed(ExecutionContext(), {"step_id": 1})
	out = await plugin.post_tool_call(ExecutionContext(), "t", {}, ToolOutput("ok result"), 3)
	assert out.content == "ok result"
	assert [p["event"] for p in payloads] == ["on_error", "on_plan_created", "on_step_completed"]


def test_safe_eval_condition_supported_and_rejected_paths():
	assert _safe_eval_condition("tool_name.startswith('r') and arguments['x'] in [1, 2]", {"tool_name": "read", "arguments": {"x": 1}})
	assert _safe_eval_condition("not False", {})
	assert not _safe_eval_condition("x" * 501, {})
	assert not _safe_eval_condition("bad(", {})
	assert not _safe_eval_condition("obj == 1", {"obj": object()})


async def test_hitl_tools_auto_approval_reject_and_ask_human():
	plugin = HumanInTheLoopPlugin()
	plugin.initialize({"ask_human": False}, None)
	assert plugin.get_tools() == []

	plugin.initialize({"auto_approve": ["safe_tool"], "timeout": 0.01}, None)
	allowed, _ = await plugin.pre_tool_call(ExecutionContext(), "safe_tool", {})
	assert allowed is True

	ctx = ExecutionContext()
	allowed, _ = await plugin.pre_tool_call(ctx, "shell_command", {"command": "rm -rf /"})
	assert allowed is False

	request_q = asyncio.Queue()
	response_q = asyncio.Queue()
	await response_q.put("yes")
	result = await plugin._ask_human(
		{"question": "Continue?", "options": ["yes", "no"]},
		{"request_queue": request_q, "response_queue": response_q},
	)
	assert result.content["answer"] == "yes"
	assert (await request_q.get())["type"] == "ask_human"
	assert (await plugin._ask_human({}, {})).is_error
	assert (await plugin._ask_human({"question": "q"}, {})).is_error
	assert _risk_level("dangerous") == 2


async def test_hitl_queue_approval_response():
	plugin = HumanInTheLoopPlugin()
	plugin.initialize({"timeout": 1}, None)
	ctx = ExecutionContext()
	ctx.runtime.approval_queue = asyncio.Queue()

	async def approve():
		req = await ctx.runtime.approval_queue.get()
		await ctx.runtime.approval_queue.put({"type": "response", "request_id": req["request_id"], "approved": True})

	task = asyncio.create_task(approve())
	allowed, _ = await plugin.pre_tool_call(ctx, "shell_command", {"command": "rm file"})
	await task
	assert allowed is True
