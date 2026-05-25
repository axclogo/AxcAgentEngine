"""Graph ToolOutput presentation."""
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
			return ToolOutput.json_output(payload, summary=summary)
		return ToolOutput(
			content=content,
			content_type="json",
			summary=summary,
			artifacts=[ref],
			metadata={"namespace": self._config.namespace, "kind": kind},
		)
