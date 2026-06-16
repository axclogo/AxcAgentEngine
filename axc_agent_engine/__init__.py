"""AxcAgentEngine — 纯 Agent 执行引擎框架。"""
from axc_agent_engine.llm.config import LLMConfig
from axc_agent_engine.runtime.concurrency import ConcurrencyConfig, ExecutionLimiter, RateLimiter, SessionExecutionGate
from axc_agent_engine.engine import AgentModels, AgentTemplate, Engine
from axc_agent_engine.agent import Agent
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.errors import ErrorEnvelope, ErrorCategory
from axc_agent_engine.core.schema import ToolDefinition, LLMMessage, LLMUsage, LLMResponse, LLMStreamChunk, Capability
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.registry import PluginRegistry
from axc_agent_engine.tools.decorator import tool
from axc_agent_engine.tools.tool_output import ToolOutput, ArtifactRef
from axc_agent_engine.storage.artifact_store import ArtifactStore, InMemoryArtifactStore
from axc_agent_engine.observability.audit import AuditEvent, AuditEventType, InMemoryAuditSink
from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus, CheckpointStore, InMemoryCheckpointStore
from axc_agent_engine.runtime.policy import CapabilityPolicyEvaluator, PolicyDecision, PolicyEvaluator, PolicyRequest
from axc_agent_engine.runtime.input import InputProviderResult, InputProvider, PassthroughInputProvider
from axc_agent_engine.runtime.resources import (
	ResourceRegistry,
	ResourceError,
	ResourceNotFoundError,
	ResourceTypeError,
	DuplicateResourceError,
)
from axc_agent_engine._version import __version__

__all__ = [
	#English: Bilingual note. 中文：核心公开 API
	"Engine", "AgentModels", "AgentTemplate", "LLMConfig", "Agent", "Event", "EventType",
	"ConcurrencyConfig", "ExecutionLimiter", "RateLimiter", "SessionExecutionGate",
	#English: Source note. 中文：审计与错误
	"AuditEvent", "AuditEventType", "InMemoryAuditSink", "ErrorEnvelope", "ErrorCategory",
	#English: durable execution 中文：源码说明。
	"Checkpoint", "CheckpointStatus", "CheckpointStore", "InMemoryCheckpointStore",
	#English: policy 中文：源码说明。
	"CapabilityPolicyEvaluator", "PolicyDecision", "PolicyEvaluator", "PolicyRequest",
	# LLM 响应模型
	"LLMMessage", "LLMUsage", "LLMResponse", "LLMStreamChunk",
	#English: Source note. 中文：能力模型
	"Capability",
	#English: Source note. 中文：工具输出
	"ToolOutput", "ArtifactRef", "ArtifactStore", "InMemoryArtifactStore",
	#English: Source note. 中文：输入和共享资源边界
	"InputProviderResult", "InputProvider", "PassthroughInputProvider",
	"ResourceRegistry", "ResourceError", "ResourceNotFoundError",
	"ResourceTypeError", "DuplicateResourceError",
	#English: Source note. 中文：插件开发
	"BasePlugin", "PluginRegistry", "ToolDefinition", "tool",
	"__version__",
]
