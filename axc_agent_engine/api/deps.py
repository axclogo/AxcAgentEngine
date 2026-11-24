"""Resolve HTTP agent names against the host engine. 通过宿主引擎解析 HTTP Agent 名称。"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.agent import Agent
	from axc_agent_engine.engine import AgentModels, Engine


@dataclass
class EngineState:
	engine: "Engine"
	models: "AgentModels"
	agents_dir: str = ""

	def get_agent(self, agent_name: str) -> "Agent | None":
		"""Resolve a loaded name or an allowed template stem. 按已加载的名称或允许的模板文件名查找 Agent。"""
		agent = self.engine.get_agent(agent_name)
		if agent is not None:
			return agent
		for path in self._templates():
			if path.stem == agent_name:
				template = self.engine.load_agent_template(str(path))
				return self.engine.get_agent(template.config.name) or template.instantiate(models=self.models)
		return None

	def list_agents(self) -> list[dict[str, str]]:
		agents = {agent.registered_name: agent.description for agent in self.engine.list_agents()}
		for path in self._templates():
			agents.setdefault(path.stem, "")
		return [{"name": name, "description": description} for name, description in agents.items()]

	def _templates(self) -> Iterator[Path]:
		if not self.agents_dir:
			return
		root = Path(self.agents_dir).resolve()
		if root.is_dir():
			for path in sorted(root.iterdir()):
				if path.suffix in {".yaml", ".yml"} and path.is_file() and path.resolve().is_relative_to(root):
					yield path
