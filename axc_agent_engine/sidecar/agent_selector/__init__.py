"""宿主侧 Agent 选择工具。
Host-side Agent selection utilities.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_WORD_RE = re.compile(r"[A-Za-z0-9_\-\u4e00-\u9fff]+")


@dataclass(frozen=True)
class AgentProfile:
	"""用于把任务路由到 Agent 的宿主侧静态画像。
	Static host-side profile used for routing tasks to Agents.
	"""
	name: str
	description: str = ""
	capabilities: set[str] = field(default_factory=set)
	tags: set[str] = field(default_factory=set)
	cost_weight: float = 1.0
	latency_weight: float = 1.0
	quality_score: float = 0.5
	risk_score: float = 0.0
	metadata: dict[str, Any] = field(default_factory=dict)

	@classmethod
	def from_agent(cls, agent: Any, **overrides: Any) -> "AgentProfile":
		return cls(
			name=str(getattr(agent, "name", "")),
			description=str(getattr(agent, "description", "")),
			capabilities=set(overrides.pop("capabilities", set())),
			tags=set(overrides.pop("tags", set())),
			cost_weight=float(overrides.pop("cost_weight", 1.0)),
			latency_weight=float(overrides.pop("latency_weight", 1.0)),
			quality_score=float(overrides.pop("quality_score", 0.5)),
			risk_score=float(overrides.pop("risk_score", 0.0)),
			metadata=dict(overrides.pop("metadata", {})),
		)


@dataclass(frozen=True)
class SelectionRequest:
	"""一次宿主侧路由决策请求。
	One host-side routing decision request.
	"""
	task: str
	required_capabilities: set[str] = field(default_factory=set)
	preferred_tags: set[str] = field(default_factory=set)
	max_agents: int = 1
	cost_sensitivity: float = 0.2
	latency_sensitivity: float = 0.1
	risk_sensitivity: float = 0.2
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSelection:
	"""带评分的 Agent 候选项。
	Scored Agent candidate.
	"""
	agent: AgentProfile
	score: float
	reasons: list[str] = field(default_factory=list)
	missing_capabilities: set[str] = field(default_factory=set)


class AgentSelector:
	"""确定性的宿主侧 Agent 选择器。
	Deterministic host-side Agent selector.
	"""

	def __init__(self, profiles: list[AgentProfile] | None = None) -> None:
		self._profiles: dict[str, AgentProfile] = {profile.name: profile for profile in profiles or []}

	def register(self, profile: AgentProfile, replace: bool = True) -> None:
		if not profile.name:
			raise ValueError("profile.name is required")
		if not replace and profile.name in self._profiles:
			raise ValueError(f"Agent profile already registered: {profile.name}")
		self._profiles[profile.name] = profile

	def list_profiles(self) -> list[AgentProfile]:
		return list(self._profiles.values())

	def select(self, request: SelectionRequest | str) -> list[AgentSelection]:
		req = request if isinstance(request, SelectionRequest) else SelectionRequest(task=str(request))
		scored = [self._score(profile, req) for profile in self._profiles.values()]
		scored.sort(key=lambda item: item.score, reverse=True)
		limit = max(1, req.max_agents)
		return scored[:limit]

	def best(self, request: SelectionRequest | str) -> AgentSelection | None:
		selected = self.select(request)
		return selected[0] if selected else None

	def _score(self, profile: AgentProfile, request: SelectionRequest) -> AgentSelection:
		task_tokens = _tokens(request.task)
		text_tokens = _tokens(" ".join([profile.name, profile.description, " ".join(profile.tags), " ".join(profile.capabilities)]))
		overlap = task_tokens & text_tokens
		reasons: list[str] = []
		score = 0.0
		if overlap:
			score += min(0.35, 0.05 * len(overlap))
			reasons.append(f"matched task terms: {', '.join(sorted(overlap)[:5])}")
		missing = request.required_capabilities - profile.capabilities
		if missing:
			score -= 1.0 + 0.2 * len(missing)
			reasons.append(f"missing capabilities: {', '.join(sorted(missing))}")
		elif request.required_capabilities:
			score += 0.4
			reasons.append("all required capabilities present")
		tag_hits = request.preferred_tags & profile.tags
		if tag_hits:
			score += min(0.2, 0.05 * len(tag_hits))
			reasons.append(f"matched tags: {', '.join(sorted(tag_hits))}")
		score += max(0.0, min(profile.quality_score, 1.0)) * 0.4
		score -= max(0.0, profile.cost_weight - 1.0) * request.cost_sensitivity
		score -= max(0.0, profile.latency_weight - 1.0) * request.latency_sensitivity
		score -= max(0.0, min(profile.risk_score, 1.0)) * request.risk_sensitivity
		return AgentSelection(agent=profile, score=round(score, 4), reasons=reasons, missing_capabilities=missing)


def _tokens(text: str) -> set[str]:
	return {match.group(0).lower() for match in _WORD_RE.finditer(text or "")}
