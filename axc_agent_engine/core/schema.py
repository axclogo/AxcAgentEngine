"""Agent YAML Schema 定义 + 全局枚举 + 核心数据结构"""
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
	from axc_agent_engine.tools.tool_output import ToolOutput


class StepStatus(StrEnum):
	"""POR 步骤状态"""
	PENDING = "pending"
	RUNNING = "running"
	DONE = "done"
	FAILED = "failed"
	SKIPPED = "skipped"


class RiskLevel(StrEnum):
	"""English: This documentation describes the related engine component behavior.
中文：工具风险等级"""
	SAFE = "safe"
	MODERATE = "moderate"
	DANGEROUS = "dangerous"
	BLOCKED = "blocked"


class PluginSignal(StrEnum):
	"""English: This documentation describes the related engine component behavior.
中文：插件通信信号。"""
	NONE = "none"
	STOP = "stop"
	WARN = "warn"
	SKIP = "skip"


class Capability(StrEnum):
	"""English: This documentation describes the related engine component behavior.
中文：用于权限控制的工具能力分类。"""
	FILE_READ = "file_read"
	FILE_WRITE = "file_write"
	SHELL = "shell"
	PYTHON_EXEC = "python_exec"
	HTTP_REQUEST = "http_request"
	PIP_INSTALL = "pip_install"
	HUMAN_APPROVAL = "human_approval"
	KNOWLEDGE_SEARCH = "knowledge_search"
	AGENT_CALL = "agent_call"


#English: Source note. 中文：── 工具定义 ──

@dataclass
class ToolDefinition:
	"""English: This documentation describes the related engine component behavior.
中文：带能力与风险元数据的类型化工具定义。"""
	name: str
	description: str = ""
	parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
	execute: Callable[..., Awaitable["ToolOutput"]] | None = None
	is_read_only: bool = False
	timeout: int = 120
	deferred: bool = False
	capability: str = ""  # Capability 枚举值；空字符串表示不限制
	risk_level: str = "safe"  # RiskLevel 枚举值

	def to_openai_schema(self) -> dict[str, Any]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
返回 OpenAI-compatible chat API 使用的 function-call schema。"""
		return {
			"type": "function",
			"function": {
				"name": self.name,
				"description": self.description,
				"parameters": self.parameters,
			},
		}


# ── LLM 标准化响应 ──

@dataclass
class LLMUsage:
	"""Token 用量统计。"""
	input_tokens: int = 0
	output_tokens: int = 0
	cached_tokens: int = 0


@dataclass
class LLMMessage:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
标准化 LLM assistant message。"""
	role: str = "assistant"
	content: str = ""
	tool_calls: list[dict[str, Any]] = field(default_factory=list)
	raw: dict[str, Any] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
转换成核心循环使用的内部 assistant message dict。"""
		d: dict[str, Any] = {"role": self.role, "content": self.content}
		if self.tool_calls:
			d["tool_calls"] = self.tool_calls
		return d


@dataclass
class LLMResponse:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
标准化非流式 LLM 响应。"""
	message: LLMMessage = field(default_factory=LLMMessage)
	usage: LLMUsage = field(default_factory=LLMUsage)
	raw: Any = None


@dataclass
class LLMStreamChunk:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
标准化流式 chunk。"""
	content_delta: str = ""
	thinking_delta: str = ""
	tool_call_delta: dict[str, Any] | None = None
	usage: LLMUsage | None = None
	finish_reason: str | None = None
	metadata: dict[str, Any] = field(default_factory=dict)
	raw: Any = None


# ── Pydantic 配置模型 ──

class RoutingConfig(BaseModel):
	"""English: This documentation describes the related engine component behavior.
中文：执行路由策略。"""
	mode: str = Field(default="auto", pattern=r"^(auto|react_only|por_first)$")
	model_config = {"extra": "forbid"}


class ConcurrencyRuntimeConfig(BaseModel):
	"""Optional runtime backpressure for one Agent.
中文：此文档说明相关引擎组件的行为。"""
	max_agent_concurrent_runs: int = Field(default=0, ge=0)
	max_session_concurrent_runs: int = Field(default=1, ge=0)
	queue_timeout: float = Field(default=0.0, ge=0)
	model_config = {"extra": "forbid"}


class RuntimeConfig(BaseModel):
	max_rounds: int = Field(default=50, ge=1, le=500)
	thinking: str = Field(default="auto", pattern=r"^(auto|always|never)$")
	parallel_tool_calls: bool = True
	human_in_the_loop: bool = False
	stream_idle_timeout: int = Field(default=60, ge=1)
	workspace: str = ""
	step_timeout: int = Field(default=300, ge=0)
	total_timeout: int = Field(default=600, ge=0)
	allowed_capabilities: list[str] = Field(default_factory=list)
	routing: RoutingConfig = Field(default_factory=RoutingConfig)
	concurrency: ConcurrencyRuntimeConfig = Field(default_factory=ConcurrencyRuntimeConfig)
	model_config = {"extra": "forbid"}


class PluginConfig(BaseModel):
	"""English: This documentation describes the related engine component behavior.
中文：单个插件配置（动态字段）"""
	enabled: bool = False
	required: bool = False
	model_config = {"extra": "allow"}


class AgentConfig(BaseModel):
	"""Agent YAML 顶层 Schema"""
	name: str
	description: str = ""
	runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
	system_prompt: str = ""
	system_prompt_file: str = ""
	plugins: dict[str, PluginConfig] = Field(default_factory=dict)
	model_config = {"extra": "forbid"}
