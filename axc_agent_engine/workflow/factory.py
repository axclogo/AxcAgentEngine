"""Workflow runtime factory. 创建工作流运行时的工厂函数。"""

from __future__ import annotations

from axc_agent_engine.workflow.memory_runtime import MemoryWorkflowRuntime
from axc_agent_engine.workflow.protocols import WorkflowRuntime


def create_workflow_runtime(prefer_burr: bool = False) -> WorkflowRuntime:
	"""Select the explicitly requested workflow. 按宿主声明选择工作流。"""
	if prefer_burr:
		from axc_agent_engine.workflow.burr_runtime import BurrWorkflowRuntime

		return BurrWorkflowRuntime()
	return MemoryWorkflowRuntime()
