"""AxcAgentEngine — 纯 Agent 执行引擎框架。"""
from axc_agent_engine.llm.config import LLMConfig
from axc_agent_engine.runtime.concurrency import ConcurrencyConfig, ExecutionLimiter, RateLimiter, SessionExecutionGate
from axc_agent_engine.engine import Engine
from axc_agent_engine.agent import Agent
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.errors import ErrorEnvelope, ErrorCategory
from axc_agent_engine.core.schema import ToolDefinition, LLMMessage, LLMUsage, LLMResponse, LLMStreamChunk, Capability
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.registry import PluginRegistry
from axc_agent_engine.tools.decorator import tool
from axc_agent_engine.tools.tool_output import ToolOutput, ArtifactRef, ResultStore
from axc_agent_engine.observability.audit import AuditEvent, AuditEventType, InMemoryAuditSink
from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus, CheckpointStore, InMemoryCheckpointStore
from axc_agent_engine.runtime.recovery import ExecutionRecoveryService, RecoverableRun
from axc_agent_engine.runtime.policy import CapabilityPolicyEvaluator, PolicyDecision, PolicyEvaluator, PolicyRequest
from axc_agent_engine.runtime.input import InputProviderResult, InputProvider, PassthroughInputProvider
from axc_agent_engine.runtime.resources import (
	ResourceRegistry,
	ResourceError,
	ResourceNotFoundError,
	ResourceTypeError,
	DuplicateResourceError,
)

__all__ = [
	# 核心公开 API
	"Engine", "LLMConfig", "Agent", "Event", "EventType",
	"ConcurrencyConfig", "ExecutionLimiter", "RateLimiter", "SessionExecutionGate",
	# 审计与错误
	"AuditEvent", "AuditEventType", "InMemoryAuditSink", "ErrorEnvelope", "ErrorCategory",
	# durable execution
	"Checkpoint", "CheckpointStatus", "CheckpointStore", "InMemoryCheckpointStore",
	"ExecutionRecoveryService", "RecoverableRun",
	# policy
	"CapabilityPolicyEvaluator", "PolicyDecision", "PolicyEvaluator", "PolicyRequest",
	# LLM 响应模型
	"LLMMessage", "LLMUsage", "LLMResponse", "LLMStreamChunk",
	# 能力模型
	"Capability",
	# 工具输出
	"ToolOutput", "ArtifactRef", "ResultStore",
	# 输入和共享资源边界
	"InputProviderResult", "InputProvider", "PassthroughInputProvider",
	"ResourceRegistry", "ResourceError", "ResourceNotFoundError",
	"ResourceTypeError", "DuplicateResourceError",
	# 插件开发
	"BasePlugin", "PluginRegistry", "ToolDefinition", "tool",
]

try:
	from importlib.metadata import version as _get_version
	__version__ = _get_version("axc-agent-engine")
except Exception:
	__version__ = "0.2.0"
