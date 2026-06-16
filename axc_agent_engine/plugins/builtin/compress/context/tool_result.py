"""L1 tool result management.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from typing import Any

from axc_agent_engine.plugins.builtin.compress.context.normalizer import estimate_tokens
from axc_agent_engine.tools.tool_output import ToolOutput


async def externalize_large_tool_output(
	output: ToolOutput,
	artifact_store: Any,
	artifact_threshold_tokens: int,
) -> ToolOutput:
	"""Move large tool output content to ArtifactStore when available.
中文：此文档说明相关引擎组件的行为。"""
	if output.is_error or artifact_store is None:
		return output
	content = output._content_as_str()
	if estimate_tokens(content) <= artifact_threshold_tokens:
		return output
	artifact = await artifact_store.put_text(content, {**output.metadata}, kind=output.content_type)
	metadata = dict(output.metadata)
	metadata.update({
		"externalized": True,
		"original_size": len(content),
		"artifact_id": artifact.id,
	})
	return ToolOutput(
		content=content,
		content_type=output.content_type,
		summary=output.summary,
		artifacts=[*output.artifacts, artifact],
		metadata=metadata,
	)
