"""Workflow runtime factory.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from axc_agent_engine.workflow.memory_runtime import MemoryWorkflowRuntime
from axc_agent_engine.workflow.protocols import WorkflowRuntime


def create_workflow_runtime(prefer_burr: bool = True) -> WorkflowRuntime:
	"""Use Burr when installed, otherwise keep the lightweight memory runtime.
中文：此文档说明相关引擎组件的行为。"""
	if prefer_burr:
		try:
			from axc_agent_engine.workflow.burr_runtime import BurrWorkflowRuntime
			return BurrWorkflowRuntime()
		except RuntimeError:
			pass
	return MemoryWorkflowRuntime()
