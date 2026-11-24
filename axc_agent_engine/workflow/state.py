"""Build a resume plan from a stored checkpoint. 从已存储的检查点构建恢复计划。"""

from __future__ import annotations

from typing import Any

from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus, ForkedRun
from axc_agent_engine.workflow.protocols import WorkflowResumePlan


def fork_checkpoint(
	checkpoint: Checkpoint, branch: ForkedRun, scopes: dict, continuation: dict | None, version: int
) -> Checkpoint:
	"""Keep the continuation and rebind its execution identity. 保留续跑状态并重新绑定执行身份。"""
	state = checkpoint.state
	seed = {key: state[key] for key in ("phase", "kind", "cursor", "messages", "strategy", "fork_state")}
	seed.update(
		strategy_state=continuation,
		state_version=version,
		run_id=branch.run_id,
		owner=state.get("owner", {}),
		usage={"input_tokens": 0, "output_tokens": 0},
		payload={},
		plugin_scopes=scopes,
		metadata={
			"agent_name": state["metadata"]["agent_name"],
			"session_id": branch.session_id,
			"run_id": branch.run_id,
		},
	)
	if "result" in state:
		seed.update(result=state["result"], result_metadata={}, cursor={"current_round": 0})
	return Checkpoint(
		run_id=branch.run_id,
		sequence=1,
		kind=checkpoint.kind,
		status=checkpoint.status,
		state=seed,
		metadata={"forked_from": {"run_id": checkpoint.run_id, "checkpoint_id": checkpoint.id}},
	)


def session_id_from_checkpoint(checkpoint: Checkpoint) -> str:
	metadata = checkpoint.state.get("metadata", {})
	if not isinstance(metadata, dict):
		raise TypeError("Invalid checkpoint metadata")
	session_id = metadata.get("session_id", checkpoint.metadata.get("session_id", ""))
	if not isinstance(session_id, str):
		raise TypeError("Invalid checkpoint session_id")
	return session_id


def resume_snapshot_from_checkpoint(checkpoint: Checkpoint) -> dict[str, Any]:
	state = checkpoint.state
	legacy_plan = checkpoint.kind == "por" and "strategy" not in state
	cursor = state.get("cursor", {})
	usage = state.get("usage", {})
	if not isinstance(cursor, dict) or not isinstance(usage, dict):
		raise TypeError("Checkpoint cursor and usage must be objects")
	snapshot = {
		"strategy": "por" if legacy_plan else state.get("strategy"),
		"strategy_state": state if legacy_plan else state.get("strategy_state"),
		"state_version": state.get("state_version", 1),
		"sequence": checkpoint.sequence,
		"owner": state.get("owner", {}),
		"session_seed": state["fork_state"]
		if checkpoint.sequence == 1 and "forked_from" in checkpoint.metadata
		else None,
		"forked_from": checkpoint.metadata.get("forked_from"),
		"inbox": state.get("inbox", {}),
		"plugin_scopes": state.get("plugin_scopes", {}),
		"unsettled_writes": state.get("unsettled_writes", {}),
		"current_round": cursor.get("current_round", 0),
		"messages": state.get("messages", []),
		"input_tokens": usage.get("input_tokens", 0),
		"output_tokens": usage.get("output_tokens", 0),
		"metadata": state.get("metadata", {}),
	}
	if (
		checkpoint.kind == "execution"
		and checkpoint.status == CheckpointStatus.COMPLETED
		and state.get("phase") == "done"
	):
		result = state.get("result")
		if not isinstance(result, str):
			raise ValueError("Completed checkpoint requires a text result")
		snapshot["result"] = result
		snapshot["result_metadata"] = state.get("result_metadata", {})
	return snapshot


async def prepare_resume_plan(request) -> WorkflowResumePlan | None:
	if not request.checkpoint_store:
		return WorkflowResumePlan(run_id=request.run_id, kind="handler")
	checkpoint = await request.checkpoint_store.latest(request.run_id)
	if not checkpoint:
		return None
	return WorkflowResumePlan(
		run_id=request.run_id,
		kind=checkpoint.kind,
		session_id=session_id_from_checkpoint(checkpoint),
		snapshot=resume_snapshot_from_checkpoint(checkpoint),
		metadata={**checkpoint.metadata, "checkpoint_id": checkpoint.id},
	)
