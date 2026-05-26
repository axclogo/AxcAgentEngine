"""Workflow state helpers."""
from __future__ import annotations

from typing import Any

from axc_agent_engine.runtime.checkpoint import Checkpoint
from axc_agent_engine.workflow.protocols import WorkflowResumePlan


def session_id_from_checkpoint(checkpoint: Checkpoint) -> str:
	metadata = checkpoint.state.get("metadata", {})
	if isinstance(metadata, dict) and metadata.get("session_id"):
		return str(metadata["session_id"])
	if checkpoint.metadata.get("session_id"):
		return str(checkpoint.metadata["session_id"])
	return ""


def resume_snapshot_from_checkpoint(checkpoint: Checkpoint) -> dict[str, Any]:
	state = checkpoint.state
	if checkpoint.kind == "por":
		return {"por_checkpoint": state, "metadata": state.get("metadata", {})}
	cursor = state.get("cursor") if isinstance(state.get("cursor"), dict) else {}
	usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
	return {
		"current_round": cursor.get("current_round", state.get("current_round", 0)),
		"messages": state.get("messages", []),
		"input_tokens": usage.get("input_tokens", state.get("input_tokens", 0)),
		"output_tokens": usage.get("output_tokens", state.get("output_tokens", 0)),
		"metadata": state.get("metadata", {}),
	}


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
		metadata={"checkpoint_id": checkpoint.id, **dict(checkpoint.metadata)},
	)
