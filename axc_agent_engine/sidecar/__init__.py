"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
宿主侧主动调用的旁路能力。
Host-driven sidecar capabilities.

旁路模块不属于 Agent 执行核心，也不会从 Agent YAML 自动加载。
Sidecar modules are not part of the Agent execution core and are not loaded from
Agent YAML.

宿主应用必须显式 import 这些模块来运行旁路任务。
Host applications import them explicitly to run sidecar tasks."""
from axc_agent_engine.sidecar.orchestration import (
	OrchestrationTask,
	OrchestrationTaskService,
	OrchestrationTaskStatus,
)
from axc_agent_engine.sidecar.agent_selector import AgentProfile, AgentSelection, AgentSelector, SelectionRequest
from axc_agent_engine.sidecar.cost_optimizer import (
	CostEstimator,
	CostFinding,
	CostOptimizationReport,
	CostReportBuilder,
	CostSample,
	ModelPrice,
)
from axc_agent_engine.sidecar.distiller import DistillationReport, DistillationReportBuilder, DistilledRule, ExecutionTrace, TraceStep
from axc_agent_engine.sidecar.failure_miner import FailureCluster, FailureMiningReport, FailureRecord, FailureReportBuilder

__all__ = [
	"AgentProfile",
	"AgentSelection",
	"AgentSelector",
	"CostEstimator",
	"CostFinding",
	"CostOptimizationReport",
	"CostReportBuilder",
	"CostSample",
	"DistillationReport",
	"DistillationReportBuilder",
	"DistilledRule",
	"ExecutionTrace",
	"FailureCluster",
	"FailureMiningReport",
	"FailureRecord",
	"FailureReportBuilder",
	"ModelPrice",
	"OrchestrationTask",
	"OrchestrationTaskService",
	"OrchestrationTaskStatus",
	"SelectionRequest",
	"TraceStep",
]
