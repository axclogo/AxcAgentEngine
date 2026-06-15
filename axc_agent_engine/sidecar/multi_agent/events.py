"""MultiAgent 事件类型"""
from copy import deepcopy
from dataclasses import dataclass, field

from axc_agent_engine.sidecar.multi_agent.types import MultiAgentEventType


@dataclass
class MultiAgentEvent:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
多 Agent 会话事件"""
	type: MultiAgentEventType
	agent_name: str = ""
	content: str = ""
	round_num: int = 0
	metadata: dict = field(default_factory=dict)

	def __post_init__(self) -> None:
		self.metadata = deepcopy(self.metadata)
