"""执行上下文 — config + state + services + 便捷方法。

ExecutionConfig：不可变执行配置（max_rounds、stream、thinking 等）。
ExecutionState：可变运行时状态（current_round、tokens、cancelled 等）。
ExecutionServices：类型化基础设施依赖（result_store、message_bus 等）。
ExecutionContext：组合以上对象，并提供 cancel、add_usage、check_cancelled 等便捷方法。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.storage.protocols import AuditSink, CheckpointStore, MessageBus, ResultStore
	from axc_agent_engine.runtime.sandbox_models import CommandExecutor
	from axc_agent_engine.runtime.policy import PolicyEvaluator
	from axc_agent_engine.plugins import AgentInfo, ModelInfo


@dataclass(frozen=True)
class ExecutionConfig:
	"""单次执行的不可变配置。"""
	system_prompt: str = ""
	max_rounds: int = 50
	stream: bool = False
	thinking: str = "auto"
	parallel_tool_calls: bool = True
	human_in_the_loop: bool = False
	stream_idle_timeout: int = 60
	step_timeout: int = 300
	total_timeout: int = 600
	workspace: str = ""
	allowed_capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ExecutionServices:
	"""执行所需的类型化基础设施依赖。"""
	result_store: "ResultStore | None" = None
	message_bus: "MessageBus | None" = None
	dispatcher: "object | None" = None
	audit_sink: "AuditSink | None" = None
	checkpoint_store: "CheckpointStore | None" = None
	command_executor: "CommandExecutor | None" = None
	policy_evaluator: "PolicyEvaluator | None" = None


@dataclass
class ExecutionState:
	"""单次执行的可变运行时状态。

	只包含通用执行状态；插件专属状态应通过类型化访问器存放在 metadata 中。
	"""
	current_round: int = 0
	total_input_tokens: int = 0
	total_output_tokens: int = 0
	cancelled: bool = False
	error: str = ""
	fallback_triggered: bool = False
	fallback_reason: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionRuntimeState:
	"""不可 checkpoint 的运行时内部状态。"""
	plugin_states: dict[str, Any] = field(default_factory=dict)
	event_queue: Any = None
	stream_delta_emitted: bool = False
	approval_queue: Any = None
	response_queue: Any = None
	agent_call_depth: int = 0
	risk_level: str = ""
	llm_options: dict[str, Any] = field(default_factory=dict)
	model_info: "ModelInfo | None" = None
	agent_info: "AgentInfo | None" = None
	cancel_source: "ExecutionState | None" = None


@dataclass
class ExecutionContext:
	"""组合 config + state + services，并提供便捷方法。

	通过 ctx.config.max_rounds、ctx.config.stream 等访问配置。
	通过 ctx.state.current_round、ctx.state.metadata 等访问状态。
	通过 ctx.services.result_store 等访问服务。
	"""
	config: ExecutionConfig = field(default_factory=ExecutionConfig)
	state: ExecutionState = field(default_factory=ExecutionState)
	services: ExecutionServices = field(default_factory=ExecutionServices)
	runtime: ExecutionRuntimeState = field(default_factory=ExecutionRuntimeState)
	utility_llm: Any = None

	# ── 便捷方法 ──

	def cancel(self) -> None:
		"""标记执行已取消。"""
		self.state.cancelled = True

	def add_usage(self, input_tokens: int, output_tokens: int) -> None:
		"""累加 token 用量。"""
		self.state.total_input_tokens += input_tokens
		self.state.total_output_tokens += output_tokens

	def estimate_image_tokens(self, messages: list[dict]) -> int:
		"""估算消息中图片的 token 成本。"""
		total = 0
		for msg in messages:
			content = msg.get("content")
			if not isinstance(content, list):
				continue
			for part in content:
				if part.get("type") == "image_url":
					detail = part.get("image_url", {}).get("detail", "auto")
					total += 85 if detail == "low" else 85 + 170 * 4
		return total

	def add_image_tokens(self, messages: list[dict]) -> None:
		"""估算并累加图片 token。"""
		image_tokens = self.estimate_image_tokens(messages)
		if image_tokens > 0:
			self.state.total_input_tokens += image_tokens

	def check_cancelled(self) -> None:
		"""如果执行已取消，则抛出 CancelledError。"""
		parent_cancelled = bool(self.runtime.cancel_source and self.runtime.cancel_source.cancelled)
		if self.state.cancelled or parent_cancelled:
			from axc_agent_engine.core.errors import CancelledError
			raise CancelledError("Execution cancelled")

	def get_plugin_state(self, plugin_name: str, factory: Any = None) -> Any:
		"""获取单次执行内的插件状态；首次访问时可用 factory 创建。"""
		plugin_states = self.runtime.plugin_states
		if plugin_name not in plugin_states:
			plugin_states[plugin_name] = factory() if factory else {}
		return plugin_states[plugin_name]

	def fork_for_child(self, metadata: dict[str, Any] | None = None) -> "ExecutionContext":
		"""为并行子任务创建隔离上下文。

		子上下文共享基础设施服务与只读运行配置，隔离轮次计数和插件运行态。
		取消信号从父上下文传播，避免并行步骤失去外部取消控制。
		"""
		child_metadata = deepcopy(_checkpoint_safe_metadata(self.state.metadata))
		if metadata:
			child_metadata.update(deepcopy(metadata))
		child_runtime = ExecutionRuntimeState(
			llm_options=deepcopy(self.runtime.llm_options),
			model_info=self.runtime.model_info,
			agent_info=self.runtime.agent_info,
			cancel_source=self.state,
		)
		return ExecutionContext(
			config=self.config,
			state=ExecutionState(metadata=child_metadata),
			services=self.services,
			runtime=child_runtime,
			utility_llm=self.utility_llm,
		)


def _checkpoint_safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
	"""复制可跨执行子上下文传播的稳定 metadata。"""
	allowed = {
		"run_id",
		"session_id",
		"agent_name",
		"model",
		"agent",
		"input_metadata",
		"input_artifacts",
	}
	return {key: value for key, value in metadata.items() if key in allowed}
