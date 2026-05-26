"""API 依赖：Engine 状态管理"""
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EngineState:
	"""API 层持有的引擎状态"""
	engine: Any  # Engine 实例
	agents_dir: str = ""
	_agents: dict[str, Any] = field(default_factory=dict)  # name -> Agent 缓存

	def get_agent(self, agent_name: str) -> Any:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
获取或加载 Agent（按名称缓存）"""
		if agent_name in self._agents:
			return self._agents[agent_name]
		yaml_path = self._resolve_yaml(agent_name)
		if not yaml_path:
			return None
		agent = self.engine.load_agent(yaml_path)
		self._agents[agent_name] = agent
		return agent

	def list_agents(self) -> list[dict[str, str]]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
列出可用 Agent"""
		result = []
		for name, agent in self._agents.items():
			result.append({"name": name, "description": getattr(agent, "description", "")})
		if self.agents_dir and os.path.isdir(self.agents_dir):
			for f in os.listdir(self.agents_dir):
				if f.endswith((".yaml", ".yml")):
					name = f.rsplit(".", 1)[0]
					if name not in self._agents:
						result.append({"name": name, "description": ""})
		return result

	def _resolve_yaml(self, agent_name: str) -> str | None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
解析 Agent 名称为 YAML 路径"""
		if os.path.exists(agent_name):
			return agent_name
		if self.agents_dir:
			for ext in (".yaml", ".yml"):
				path = os.path.join(self.agents_dir, agent_name + ext)
				if os.path.exists(path):
					return path
		return None
