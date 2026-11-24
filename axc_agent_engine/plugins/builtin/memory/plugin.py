"""Memory 插件 — 记忆提取/存储/召回/衰减（KVStore 持久化）"""

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from axc_agent_engine.core.errors import LLMError
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.common import bounded_int
from axc_agent_engine.plugins.builtin.config_schemas import MEMORY_CONFIG_SCHEMA
from axc_agent_engine.plugins.builtin.memory.support.service import (
	MemoryLayer,
	MemoryService,
	char_similarity,
	parse_facts_response,
)

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext

from axc_agent_engine.plugins.builtin.memory.prompts import EXTRACT_PROMPT

logger = logging.getLogger(__name__)

_MEMORY_KEY_PREFIX = "memory:"
_DEFAULT_SENSITIVE_PATTERNS = [
	r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}",
	r"\b(?:\+?\d[\d\s\-()]{7,}\d)\b",
	r"\b\d{13,19}\b",
]


def _scope_component(value: str) -> str:
	return value.replace("%", "%25").replace("|", "%7C").replace("=", "%3D").replace(":", "%3A")


class MemoryScopeResolver:
	def __init__(self, namespace: str, scope_keys: list[str], include_session_scope: bool) -> None:
		self.namespace = namespace
		self.scope_keys = scope_keys
		self.include_session_scope = include_session_scope

	@property
	def is_private(self) -> bool:
		return self.include_session_scope or "session_id" in self.scope_keys or "run_id" in self.scope_keys

	def scope_id(self, exec_ctx: "ExecutionContext | None") -> str:
		return self.from_metadata(exec_ctx.state.metadata if exec_ctx else {})

	def from_metadata(self, metadata: dict) -> str:
		parts = [_scope_component(self.namespace)]
		for key in self.scope_keys:
			value = metadata.get(key)
			if value not in (None, ""):
				parts.append(f"{_scope_component(key)}={_scope_component(str(value))}")
		if self.include_session_scope and metadata.get("session_id"):
			parts.append(f"session_id={_scope_component(str(metadata['session_id']))}")
		return "|".join(parts)

	def key_prefix(self, scope: str) -> str:
		prefix = "memory-v2:" if "%" in scope else _MEMORY_KEY_PREFIX
		return f"{prefix}{scope}:"


class MemoryPrivacyPolicy:
	def __init__(self, policy: str, patterns: list[Any]) -> None:
		self.policy = policy
		self.patterns = patterns

	def sanitize(self, content: str) -> tuple[str, dict[str, Any]]:
		text = str(content).strip()
		matches = 0
		for pattern in self.patterns:
			found = pattern.findall(text)
			if found:
				matches += len(found)
				if self.policy == "reject":
					return text, {"redacted": False, "rejected": True, "matches": matches}
				if self.policy == "redact":
					text = pattern.sub("[REDACTED]", text)
		return text, {"redacted": matches > 0 and self.policy == "redact", "rejected": False, "matches": matches}


class MemoryRepository:
	def __init__(self, store: Any, scope_resolver: MemoryScopeResolver) -> None:
		self.store = store
		self.scope_resolver = scope_resolver

	async def load_scope(self, scope: str) -> list[dict]:
		if not self.store:
			return []
		records: list[dict] = []
		prefix = self.scope_resolver.key_prefix(scope)
		for key in await self.store.list_keys(prefix):
			if ":" in key[len(prefix) :]:
				continue
			data = await self.store.get(key)
			if data and key == f"{prefix}{data.get('id')}" and data.get("metadata", {}).get("scope", scope) == scope:
				records.append(data)
		return records

	async def save_memory(self, scope: str, memory: dict) -> None:
		if not self.store:
			return
		await self.store.set(f"{self.scope_resolver.key_prefix(scope)}{memory['id']}", memory)

	async def get_memory(self, scope: str, memory_id: str) -> dict | None:
		if not self.store:
			return None
		return await self.store.get(f"{self.scope_resolver.key_prefix(scope)}{memory_id}")

	async def delete_memories(self, scope: str, ids: list[str]) -> None:
		if not self.store:
			return
		prefix = self.scope_resolver.key_prefix(scope)
		for mem_id in ids:
			await self.store.delete(f"{prefix}{mem_id}")

	async def persist_memories(self, scope: str, memories: list[dict], ids: list[str]) -> None:
		if not self.store or not ids:
			return
		by_id = {item["id"]: item for item in memories}
		for mem_id in ids:
			item = by_id.get(mem_id)
			if item:
				await self.save_memory(scope, item)


class MemoryExtractionService:
	def __init__(self, min_content_length: int) -> None:
		self.min_content_length = min_content_length

	async def extract_with_llm(
		self,
		conversation: str,
		llm: Any,
		exec_ctx: "ExecutionContext | None",
		add_memory: Any,
		is_duplicate: Any,
		valid_fact: Any,
		on_failure: Any,
	) -> None:
		prompt = EXTRACT_PROMPT.format(conversation=conversation)
		try:
			content = await llm.ask(prompt)
			if not content.strip():
				return
			json_facts = parse_facts_response(content)
			if json_facts:
				for item in json_facts:
					if not valid_fact(item):
						continue
					if len(item["content"]) >= self.min_content_length and not is_duplicate(item["content"]):
						layer = MemoryLayer.EPISODIC if item.get("type") == "episodic" else MemoryLayer.SEMANTIC
						await add_memory(
							item["content"],
							item.get("importance", 0.5),
							fact_type=item.get("type", "fact"),
							layer=layer,
							source="auto_extract",
							exec_ctx=exec_ctx,
						)
				return
			for line in content.strip().split("\n"):
				line = line.strip()
				if not line or "|" not in line:
					continue
				parts = line.split("|", 1)
				try:
					importance = float(parts[0].strip())
					fact = parts[1].strip()
				except (ValueError, IndexError):
					continue
				if len(fact) >= self.min_content_length and not is_duplicate(fact):
					await add_memory(fact, min(max(importance, 0.0), 1.0), source="auto_extract", exec_ctx=exec_ctx)
		except (LLMError, OSError, RuntimeError, ValueError) as e:
			await on_failure(e)


class MemoryPlugin(BasePlugin):
	"""Persist memory through the host key-value store.
	记忆系统 — 依赖 KVStore Protocol 持久化

	        配置项：
	          context_budget: int = 2000          # 上下文注入字符预算
	          decay_half_life: int = 7            # 衰减半衰期（天）
	          dedup_threshold: float = 0.85       # 去重相似度阈值
	          min_content_length: int = 50        # 最小记忆内容长度"""

	name = "memory"
	display_name = "记忆系统"
	priority = 20
	version = "2.0.0"
	config_schema = MEMORY_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._context_budget = config.get("context_budget", 2000)
		self._decay_half_life = config.get("decay_half_life", 7)
		self._dedup_threshold = config.get("dedup_threshold", 0.85)
		self._min_content_length = config.get("min_content_length", 50)
		self._scope_resolver = MemoryScopeResolver(
			config.get("namespace", "default"),
			config.get("scope_keys", ["tenant_id", "user_id", "agent_name"]),
			config.get("session_scope", False),
		)
		self._privacy_policy = MemoryPrivacyPolicy(
			config.get("sensitive_policy", "redact"),
			[re.compile(pattern) for pattern in config.get("sensitive_patterns", _DEFAULT_SENSITIVE_PATTERNS)],
		)
		self._max_memories = config.get("max_memories", 10000)
		self._ttl_days = config.get("ttl_days", 0)
		self._auto_extract = config.get("auto_extract", True)
		self._plugin_ctx = plugin_ctx
		self._repository = MemoryRepository(plugin_ctx.kv_store, self._scope_resolver)
		self._extraction_service = MemoryExtractionService(self._min_content_length)
		self._scopes: dict[str, ScopeMemory] = {}

	def restore_legacy_scope(self, saved: dict, supplied: dict) -> str:
		identity = dict(saved)
		for key in self._scope_resolver.scope_keys:
			if key in {"agent_name", "session_id"}:
				continue
			stored = saved.get(key)
			if key in supplied and stored not in (None, "") and supplied[key] != stored:
				raise ValueError(f"Resume metadata conflicts with checkpoint memory scope key: {key}")
			value = supplied.get(key, stored)
			if value is None or not str(value).strip():
				raise ValueError(f"Legacy checkpoint lacks memory scope. Supply original resume metadata for: {key}")
			identity[key] = value
		return self._scope_resolver.from_metadata(identity)

	def fork_scope(self, scope: str | None, source: dict, target: dict, state: dict | None) -> str:
		if state is not None and ("memories" in state) != self._scope_resolver.is_private:
			raise ValueError("Fork requires the recorded memory scope configuration")
		for key in self._scope_resolver.scope_keys:
			value = source.get(key)
			if key not in {"agent_name", "session_id", "run_id"} and (value is None or not str(value).strip()):
				raise ValueError(f"Fork requires original memory scope key: {key}")
		if scope != self._scope_resolver.from_metadata(source):
			raise ValueError("Fork identity conflicts with checkpoint memory scope")
		return self._scope_resolver.from_metadata(target)

	def capture_session(self, ctx: "ExecutionContext") -> dict:
		if self._scope_resolver.is_private:
			return {"memories": self._scope(ctx).snapshot_records()}
		return {}

	async def restore_session(self, ctx: "ExecutionContext", state: dict) -> None:
		if "memories" not in state:
			return
		await self._scope(ctx).restore_records(state["memories"])

	def _scope(self, ctx: "ExecutionContext | None") -> "ScopeMemory":
		key = self._scope_resolver.scope_id(ctx)
		if ctx is not None:
			key = ctx.runtime.plugin_scopes.setdefault(self.name, key)
		if key not in self._scopes:
			self._scopes[key] = ScopeMemory(self, key)
		return self._scopes[key]

	async def on_execution_start(self, exec_ctx: "ExecutionContext") -> None:
		await self._scope(exec_ctx).on_execution_start(exec_ctx)

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str = "") -> str:
		return self._scope(exec_ctx).inject_context(exec_ctx, topic)

	async def on_round_end(
		self, exec_ctx: "ExecutionContext", user_message: str, assistant_message: str, tool_calls: list[dict]
	) -> None:
		await self._scope(exec_ctx).on_round_end(exec_ctx, user_message, assistant_message, tool_calls)

	async def on_execution_end(self, exec_ctx: "ExecutionContext", result: str, error: str) -> None:
		await self._scope(exec_ctx).on_execution_end(exec_ctx)

	def get_tools(self) -> list[ToolDefinition]:
		"""Expose memory and tiered recall tools.
		提供 memory_add 和分层记忆工具。"""
		return [
			ToolDefinition(
				name="memory_add",
				description="手动添加一条记忆",
				parameters={
					"type": "object",
					"properties": {
						"content": {"type": "string", "description": "要记忆的内容"},
						"importance": {"type": "number", "description": "重要程度 0.0-1.0", "default": 0.7},
						"layer": {
							"type": "string",
							"description": "记忆层级",
							"enum": [item.value for item in MemoryLayer],
							"default": MemoryLayer.SEMANTIC.value,
						},
						"fact_type": {"type": "string", "description": "事实类型", "default": "fact"},
					},
					"required": ["content"],
				},
				is_read_only=False,
				capability="memory_write",
				risk_level="moderate",
				execute=self._tool_memory_add,
			),
			ToolDefinition(
				name="memory_add_fact",
				description="添加事实/偏好类长期记忆",
				parameters={
					"type": "object",
					"properties": {
						"content": {"type": "string", "description": "事实内容"},
						"importance": {"type": "number", "description": "重要程度 0.0-1.0", "default": 0.7},
						"fact_type": {"type": "string", "description": "事实类型", "default": "fact"},
					},
					"required": ["content"],
				},
				is_read_only=False,
				capability="memory_write",
				risk_level="moderate",
				execute=self._tool_memory_add_fact,
			),
			ToolDefinition(
				name="memory_add_lesson",
				description="添加经验教训记忆",
				parameters={
					"type": "object",
					"properties": {
						"content": {"type": "string", "description": "经验教训内容"},
						"importance": {"type": "number", "description": "重要程度 0.0-1.0", "default": 0.9},
					},
					"required": ["content"],
				},
				is_read_only=False,
				capability="memory_write",
				risk_level="moderate",
				execute=self._tool_memory_add_lesson,
			),
			ToolDefinition(
				name="memory_search",
				description="搜索当前作用域内的记忆",
				parameters={
					"type": "object",
					"properties": {
						"query": {"type": "string", "description": "搜索主题"},
						"layer": {"type": "string", "enum": [item.value for item in MemoryLayer]},
						"top_k": {"type": "integer", "default": 5},
					},
				},
				is_read_only=True,
				capability="memory_read",
				risk_level="safe",
				execute=self._tool_memory_search,
			),
			ToolDefinition(
				name="memory_list",
				description="列出当前作用域内的记忆",
				parameters={
					"type": "object",
					"properties": {
						"layer": {"type": "string", "enum": [item.value for item in MemoryLayer]},
						"limit": {"type": "integer", "default": 20},
					},
				},
				is_read_only=True,
				capability="memory_read",
				risk_level="safe",
				execute=self._tool_memory_list,
			),
			ToolDefinition(
				name="memory_delete",
				description="删除当前作用域内的一条记忆",
				parameters={
					"type": "object",
					"properties": {
						"id": {"type": "string", "description": "记忆 ID"},
					},
					"required": ["id"],
				},
				is_read_only=False,
				capability="memory_delete",
				risk_level="dangerous",
				execute=self._tool_memory_delete,
			),
			ToolDefinition(
				name="memory_export",
				description="导出当前作用域内的记忆",
				parameters={
					"type": "object",
					"properties": {
						"layer": {"type": "string", "enum": [item.value for item in MemoryLayer]},
					},
				},
				is_read_only=True,
				capability="memory_read",
				risk_level="safe",
				execute=self._tool_memory_export,
			),
		]

	async def _tool_memory_add(self, args: dict, context: dict):
		"""memory_add 工具实现"""
		return await self._scope(context.get("exec_ctx")).add(args, context)

	async def _tool_memory_add_fact(self, args: dict, context: dict):
		return await self._scope(context.get("exec_ctx")).add_fact(args, context)

	async def _tool_memory_add_lesson(self, args: dict, context: dict):
		return await self._scope(context.get("exec_ctx")).add_lesson(args, context)

	async def _tool_memory_search(self, args: dict, context: dict):
		return await self._scope(context.get("exec_ctx")).search(args, context)

	async def _tool_memory_list(self, args: dict, context: dict):
		return await self._scope(context.get("exec_ctx")).list_memories(args, context)

	async def _tool_memory_delete(self, args: dict, context: dict):
		return await self._scope(context.get("exec_ctx")).delete(args, context)

	async def _tool_memory_export(self, args: dict, context: dict):
		return await self._scope(context.get("exec_ctx")).export(args, context)


class ScopeMemory:
	def __init__(self, plugin: MemoryPlugin, key: str) -> None:
		self.plugin = plugin
		self.key = key
		self._service = MemoryService(
			dedup_threshold=plugin._dedup_threshold,
			decay_half_life_days=plugin._decay_half_life,
		)
		self.loaded = False
		self._lock = asyncio.Lock()
		self._pending_access: set[str] = set()
		self._stats = {
			"loaded": 0,
			"added": 0,
			"duplicates": 0,
			"deleted": 0,
			"searches": 0,
			"redacted": 0,
			"rejected_sensitive": 0,
			"conflicts": 0,
			"extraction_failures": 0,
		}

	async def add(self, args: dict, context: dict):
		return await self.add_memory_result(
			content=args.get("content", ""),
			importance=args.get("importance", 0.7),
			layer=args.get("layer", MemoryLayer.SEMANTIC),
			fact_type=args.get("fact_type", "fact"),
			exec_ctx=context.get("exec_ctx"),
		)

	async def add_fact(self, args: dict, context: dict):
		return await self.add_memory_result(
			content=args.get("content", ""),
			importance=args.get("importance", 0.7),
			layer=MemoryLayer.SEMANTIC,
			fact_type=args.get("fact_type", "fact"),
			exec_ctx=context.get("exec_ctx"),
		)

	async def add_lesson(self, args: dict, context: dict):
		return await self.add_memory_result(
			content=args.get("content", ""),
			importance=args.get("importance", 0.9),
			layer=MemoryLayer.LESSON,
			fact_type="lesson",
			exec_ctx=context.get("exec_ctx"),
		)

	async def add_memory_result(
		self, content: str, importance: float, layer: str, fact_type: str, exec_ctx: "ExecutionContext | None" = None
	):
		from axc_agent_engine.tools.tool_output import ToolOutput

		if not content:
			return ToolOutput.error("content 不能为空")
		try:
			layer_value = MemoryLayer(str(layer))
		except ValueError:
			return ToolOutput.error(f"invalid memory layer: {layer}")
		async with self._lock:
			await self._load_memories(exec_ctx)
			sanitized, privacy = self.plugin._privacy_policy.sanitize(content)
			if privacy["rejected"]:
				return ToolOutput.error("memory rejected by sensitive content policy")
			if self._is_duplicate(sanitized):
				self._stats["duplicates"] += 1
				self._sync_metadata(exec_ctx)
				return ToolOutput.json_output({"status": "duplicate", "message": "记忆已存在"})
			conflict = self._find_conflict(sanitized, fact_type, str(layer_value))
			await self._add_memory(
				content,
				importance,
				fact_type=fact_type,
				layer=layer_value,
				exec_ctx=exec_ctx,
				metadata={"manual": True},
			)
		payload = {
			"status": "ok",
			"message": "记忆已添加",
			"layer": str(layer_value),
			"fact_type": fact_type,
			"redacted": privacy["redacted"],
			"conflict": bool(conflict),
			"conflict_with": conflict["id"] if conflict else "",
		}
		return ToolOutput.json_output(
			payload,
			summary=f"记忆已添加到 {layer_value}",
		)

	async def search(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput

		exec_ctx = context.get("exec_ctx")
		query = str(args.get("query", ""))
		layer = args.get("layer")
		top_k = bounded_int(args.get("top_k", 5), 1, 50)
		async with self._lock:
			await self._load_memories(exec_ctx)
			items = self._service.retrieve(query, layer=layer, top_k=top_k)
			self._stats["searches"] += 1
			await self._persist_memories([item.id for item in items])
			self._sync_metadata(exec_ctx)
		memories = [_public_memory(item.to_dict()) for item in items]
		return ToolOutput(
			content={"memories": memories, "query": query},
			content_type="json",
			summary=f"找到 {len(items)} 条记忆",
			llm_view=_memory_search_llm_view(query, memories),
		)

	async def list_memories(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput

		exec_ctx = context.get("exec_ctx")
		layer = args.get("layer")
		limit = bounded_int(args.get("limit", 20), 1, 200)
		async with self._lock:
			await self._load_memories(exec_ctx)
			items = [item for item in self._service.items if not layer or item.layer == str(layer)]
			items.sort(key=lambda item: item.created_at, reverse=True)
			self._sync_metadata(exec_ctx)
		return ToolOutput.json_output(
			{"memories": [_public_memory(item.to_dict()) for item in items[:limit]], "count": len(items)},
			summary=f"列出 {min(len(items), limit)} 条记忆",
		)

	async def delete(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput

		exec_ctx = context.get("exec_ctx")
		mem_id = str(args.get("id", ""))
		if not mem_id:
			return ToolOutput.error("id 不能为空")
		async with self._lock:
			await self._load_memories(exec_ctx)
			deleted = self._service.store.delete_item(mem_id)
			if deleted:
				await self.plugin._repository.delete_memories(self.key, [mem_id])
				self._stats["deleted"] += 1
				await self._audit_memory(
					exec_ctx, "deleted", mem_id, capability="memory_delete", risk_level="dangerous"
				)
			self._sync_metadata(exec_ctx)
		return ToolOutput.json_output(
			{"deleted": deleted, "id": mem_id}, summary="记忆已删除" if deleted else "记忆不存在"
		)

	async def export(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput

		exec_ctx = context.get("exec_ctx")
		layer = args.get("layer")
		async with self._lock:
			await self._load_memories(exec_ctx)
			memories = [
				_public_memory(item) for item in self._service.dump() if not layer or item.get("layer") == str(layer)
			]
			self._sync_metadata(exec_ctx)
		return ToolOutput.json_output(
			{"memories": memories, "count": len(memories)}, summary=f"导出 {len(memories)} 条记忆"
		)

	def snapshot_records(self) -> list[dict]:
		return self._service.dump()

	async def restore_records(self, records: list[dict]) -> None:
		async with self._lock:
			for item in records:
				item["metadata"]["scope"] = self.key
				await self.plugin._repository.save_memory(self.key, item)
			self._service.load(records)
			self.loaded = True

	async def _load_memories(self, exec_ctx: "ExecutionContext | None" = None) -> None:
		if not self.loaded:
			records = await self.plugin._repository.load_scope(self.key)
			self._service.load(records)
			self.loaded = True
			self._stats["loaded"] = len(records)
		self._sync_metadata(exec_ctx)

	async def on_execution_start(self, exec_ctx: "ExecutionContext") -> None:
		async with self._lock:
			await self._load_memories(exec_ctx)

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str) -> str:
		before = _access_snapshot(self._service.dump())
		context = self._service.build_context(topic, budget_chars=self.plugin._context_budget)
		self._pending_access.update(_changed_access_ids(before, self._service.dump()))
		self._sync_metadata(exec_ctx)
		return context

	async def on_round_end(
		self, exec_ctx: "ExecutionContext", user_message: str, assistant_message: str, tool_calls: list[dict]
	) -> None:
		"""Extract facts at the end of a conversation round.
		调 utility_model 从对话中提取事实"""
		if not self.plugin._auto_extract or (not user_message and not assistant_message):
			return
		conversation = ""
		if user_message:
			conversation += f"用户: {user_message}\n"
		if assistant_message:
			conversation += f"助手: {assistant_message}\n"
		utility_model = self.plugin._plugin_ctx.utility_model if self.plugin._plugin_ctx else None
		async with self._lock:
			if utility_model:
				await self._extract_with_llm(conversation, utility_model, exec_ctx)
			else:
				if user_message and len(user_message) >= self.plugin._min_content_length:
					await self._add_memory(user_message, 0.5, exec_ctx=exec_ctx)

	async def on_execution_end(self, exec_ctx: "ExecutionContext") -> None:
		"""Expire stale low-scoring memories at the end of a run. 执行结束时做记忆衰减 — 移除过期低分记忆"""
		async with self._lock:
			await self._execution_end_locked(exec_ctx)
			if self._pending_access:
				await self._persist_memories(list(self._pending_access))
				self._pending_access.clear()

	async def _execution_end_locked(self, exec_ctx: "ExecutionContext") -> None:

		if not self._service.dump():
			return
		expired_ids = self._service.remove_decayed(threshold=0.05)
		if self.plugin._ttl_days > 0:
			expired_ids = list(set(expired_ids) | set(self._ttl_expired_ids()))
		if expired_ids:
			await self.plugin._repository.delete_memories(self.key, expired_ids)
			self._stats["deleted"] += len(expired_ids)
			self._sync_metadata(exec_ctx)
			logger.info(f"[memory] Decay removed {len(expired_ids)} memories")

	async def _extract_with_llm(self, conversation: str, llm: Any, exec_ctx: "ExecutionContext | None" = None) -> None:
		"""Ask the model to extract facts.
		调 LLM 提取事实"""

		async def on_failure(e: Exception) -> None:
			self._stats["extraction_failures"] += 1
			self._sync_metadata(exec_ctx)
			logger.warning(f"[memory] LLM fact extraction failed: {e}")

		await self.plugin._extraction_service.extract_with_llm(
			conversation,
			llm,
			exec_ctx,
			self._add_memory,
			self._is_duplicate,
			self._valid_extracted_fact,
			on_failure,
		)

	async def _add_memory(
		self,
		content: str,
		importance: float,
		source: str = "",
		fact_type: str = "fact",
		layer: str = MemoryLayer.SEMANTIC,
		exec_ctx: "ExecutionContext | None" = None,
		metadata: dict[str, Any] | None = None,
	) -> None:
		"""Add a memory record. 添加一条记忆"""
		sanitized, privacy = self.plugin._privacy_policy.sanitize(content)
		if privacy["rejected"]:
			self._stats["rejected_sensitive"] += 1
			self._sync_metadata(exec_ctx)
			return
		if privacy["redacted"]:
			self._stats["redacted"] += 1
		conflict = self._find_conflict(sanitized, fact_type, str(layer))
		item_metadata = dict(metadata or {})
		item_metadata.update(
			{
				"scope": self.key,
				"privacy": privacy,
				"conflict_with": conflict["id"] if conflict else "",
			}
		)
		if conflict:
			self._stats["conflicts"] += 1
		item = self._service.add(
			sanitized, layer=layer, fact_type=fact_type, importance=importance, source=source, metadata=item_metadata
		)
		mem = item.to_dict()

		removed_ids = self._enforce_capacity()
		if removed_ids:
			await self.plugin._repository.delete_memories(self.key, removed_ids)
		if mem["id"] not in removed_ids:
			await self.plugin._repository.save_memory(self.key, mem)
			self._stats["added"] += 1
			await self._audit_memory(exec_ctx, "added", mem["id"], capability="memory_write", risk_level="moderate")
		self._sync_metadata(exec_ctx)

	def _is_duplicate(self, content: str) -> bool:
		"""Deduplicate using character similarity. 简单去重：字符级相似度"""

		return self._service.find_duplicate(content) is not None

	def _find_conflict(self, content: str, fact_type: str, layer: str) -> dict | None:
		if not fact_type:
			return None
		negative = _is_negative_fact(content)
		for item in self._service.dump():
			if item.get("layer") != layer or item.get("fact_type") != fact_type:
				continue
			if (
				_is_negative_fact(item.get("content", "")) != negative
				and char_similarity(_normalize_fact_text(content), _normalize_fact_text(item.get("content", ""))) >= 0.4
			):
				return item
		return None

	def _ttl_expired_ids(self) -> list[str]:
		if self.plugin._ttl_days <= 0:
			return []
		cutoff = datetime.now(UTC) - timedelta(days=self.plugin._ttl_days)
		expired = []
		for item in self._service.items:
			if item.layer in (MemoryLayer.IDENTITY, MemoryLayer.LESSON):
				continue
			try:
				created = datetime.fromisoformat(item.created_at)
			except ValueError:
				continue
			if created < cutoff:
				expired.append(item.id)
		if expired:
			self._service.items = [item for item in self._service.items if item.id not in set(expired)]

		return expired

	def _enforce_capacity(self) -> list[str]:
		if self.plugin._max_memories <= 0 or len(self._service.items) <= self.plugin._max_memories:
			return []
		items = sorted(self._service.items, key=lambda item: (item.importance, item.decay_score, item.created_at))
		remove_ids = {item.id for item in items[: len(items) - self.plugin._max_memories]}
		self._service.items = [item for item in self._service.items if item.id not in remove_ids]

		return list(remove_ids)

	async def _persist_memories(self, ids: list[str]) -> None:
		await self.plugin._repository.persist_memories(self.key, self._service.dump(), ids)

	async def _audit_memory(
		self, exec_ctx: "ExecutionContext | None", action: str, memory_id: str, capability: str, risk_level: str
	) -> None:
		if not exec_ctx or not exec_ctx.services.audit_sink:
			return
		from axc_agent_engine.observability.audit import AuditEvent

		metadata = exec_ctx.state.metadata
		await exec_ctx.services.audit_sink.record(
			AuditEvent(
				type=f"memory_{action}",
				actor=str(metadata.get("user_id") or metadata.get("agent_name") or ""),
				session_id=str(metadata.get("session_id") or ""),
				tool_name="memory",
				capability=capability,
				risk_level=risk_level,
				metadata={"memory_id": memory_id, "scope": self.key},
			)
		)

	def _valid_extracted_fact(self, item: dict[str, Any]) -> bool:
		content = str(item.get("content", "")).strip()
		if not content:
			return False
		importance = item.get("importance", 0.5)
		try:
			float(importance)
		except (TypeError, ValueError):
			return False
		return True

	def _sync_metadata(self, exec_ctx: "ExecutionContext | None") -> None:
		if not exec_ctx:
			return
		exec_ctx.state.metadata["memory"] = {
			"scope": self.key,
			"count": len(self._service.items),
			"stats": dict(self._stats),
			"layers": _layer_counts(self._service.dump()),
		}


def _access_snapshot(memories: list[dict]) -> dict[str, tuple[str, int]]:
	return {
		str(item.get("id")): (str(item.get("last_accessed_at") or ""), int(item.get("access_count", 0) or 0))
		for item in memories
	}


def _changed_access_ids(before: dict[str, tuple[str, int]], after: list[dict]) -> list[str]:
	changed: list[str] = []
	for item in after:
		mem_id = str(item.get("id"))
		current = (str(item.get("last_accessed_at") or ""), int(item.get("access_count", 0) or 0))
		if before.get(mem_id) != current:
			changed.append(mem_id)
	return changed


def _public_memory(item: dict) -> dict:
	return {
		"id": item.get("id", ""),
		"layer": item.get("layer", ""),
		"content": item.get("content", ""),
		"fact_type": item.get("fact_type", ""),
		"importance": item.get("importance", 0.0),
		"confidence": item.get("confidence", 0.0),
		"source": item.get("source", ""),
		"created_at": item.get("created_at", ""),
		"last_accessed_at": item.get("last_accessed_at", ""),
		"access_count": item.get("access_count", 0),
		"metadata": item.get("metadata", {}),
	}


def _memory_search_llm_view(query: str, memories: list[dict]) -> str:
	if not memories:
		return f"Memory search\nQuery: {query}\nMemories: none"
	lines = [f"Memory search\nQuery: {query}", f"Memories: {len(memories)}"]
	for index, item in enumerate(memories, start=1):
		layer = item.get("layer", "")
		importance = item.get("importance", "")
		fact_type = item.get("fact_type", "")
		content = str(item.get("content", "")).strip()
		header = f"{index}. layer={layer}"
		if fact_type:
			header += f" type={fact_type}"
		if importance != "":
			header += f" importance={importance}"
		lines.append(header)
		if content:
			lines.append(f"   {content}")
	return "\n".join(lines)


def _layer_counts(memories: list[dict]) -> dict[str, int]:
	counts: dict[str, int] = {}
	for item in memories:
		layer = str(item.get("layer", ""))
		counts[layer] = counts.get(layer, 0) + 1
	return counts


def _is_negative_fact(content: str) -> bool:
	text = str(content).lower()
	return any(
		marker in text for marker in (" not ", " never ", " dislike", " don't ", "doesn't", "不", "没有", "讨厌")
	)


def _normalize_fact_text(content: str) -> str:
	text = str(content).lower()
	for marker in (" not ", " never ", " dislike", " don't ", "doesn't", "不", "没有", "讨厌"):
		text = text.replace(marker, " ")
	return " ".join(text.split())
