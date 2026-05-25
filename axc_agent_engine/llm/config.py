"""LLM 配置。"""
from dataclasses import dataclass, field
from typing import Any

from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig


@dataclass
class LLMConfig:
	"""LLM 连接配置。"""
	base_url: str
	api_key: str
	model: str
	temperature: float = 0.7
	max_tokens: int | None = None
	timeout: int = 120
	extra_params: dict[str, Any] = field(default_factory=dict)
	tool_name_mapping: ToolNameMappingConfig | None = field(default_factory=ToolNameMappingConfig)
	max_concurrent_requests: int = 0
	requests_per_minute: int = 0
	rate_limit_queue_timeout: float = 0.0
