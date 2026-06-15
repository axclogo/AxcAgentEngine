"""Graph ToolOutput presentation.
中文：此文档说明相关引擎组件的行为。"""
import json
import logging

from axc_agent_engine.plugins.builtin.common import externalize_text, result_store_from_context

from .config import GraphConfig

logger = logging.getLogger(__name__)


class GraphPresenter:
	def __init__(self, config: GraphConfig, plugin_ctx) -> None:
		self._config = config
		self._plugin_ctx = plugin_ctx

	async def json_output(self, payload: dict, context: dict, summary: str, kind: str):
		from axc_agent_engine.tools.tool_output import ToolOutput
		encoded = json.dumps(payload, ensure_ascii=False, default=str)
		store = result_store_from_context(context, self._plugin_ctx)
		content, ref = await externalize_text(
			encoded,
			store,
			self._config.max_result_bytes,
			{"kind": kind, "namespace": self._config.namespace},
			logger,
			"graph",
			2000,
		)
		if ref is None:
			return ToolOutput(
				content=payload,
				content_type="json",
				summary=summary,
				llm_view=_graph_llm_view(payload, kind, summary),
			)
		return ToolOutput(
			content=content,
			content_type="json",
			summary=summary,
			llm_view=_graph_llm_view(payload, kind, summary),
			artifacts=[ref],
			metadata={"namespace": self._config.namespace, "kind": kind},
		)


def _graph_llm_view(payload: dict, kind: str, summary: str) -> str:
	lines = [f"Graph result: {kind}", summary]
	entities = payload.get("entities")
	relations = payload.get("relations")
	if isinstance(entities, list):
		lines.append(f"Entities: {len(entities)}")
		for index, entity in enumerate(entities[:10], start=1):
			if not isinstance(entity, dict):
				continue
			name = entity.get("name", entity.get("id", ""))
			entity_type = entity.get("entity_type", entity.get("type", ""))
			description = str(entity.get("description", "")).strip()
			header = f"{index}. {name}"
			if entity_type:
				header += f" ({entity_type})"
			lines.append(header)
			if description:
				lines.append(f"   {description}")
	if isinstance(relations, list):
		lines.append(f"Relations: {len(relations)}")
		for index, relation in enumerate(relations[:10], start=1):
			if not isinstance(relation, dict):
				continue
			source = relation.get("source", relation.get("source_id", ""))
			target = relation.get("target", relation.get("target_id", ""))
			relation_type = relation.get("relation_type", relation.get("type", "RELATED_TO"))
			description = str(relation.get("description", "")).strip()
			lines.append(f"{index}. {source} -[{relation_type}]-> {target}")
			if description:
				lines.append(f"   {description}")
	return "\n".join(str(line) for line in lines if line != "")
