"""Memory 插件 — 记忆提取/存储/召回/衰减（KVStore 持久化）"""
import asyncio
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, TYPE_CHECKING

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.builtin.memory.support.embedding import HashEmbeddingClient, OpenAICompatibleEmbeddingClient
from axc_agent_engine.plugins.builtin.memory.support.service import MemoryLayer, MemoryService, char_similarity, parse_facts_response
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.common import bounded_int

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext

from axc_agent_engine.plugins.builtin.memory.prompts import EXTRACT_PROMPT

logger = logging.getLogger(__name__)

_MEMORY_KEY_PREFIX = "memory:"
_DEFAULT_SENSITIVE_PATTERNS = [
	r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}",
	r"\b(?:\+?\d[\d\s\-()]{7,}\d)\b",
	r"\b\d{13,19}\b",
]


class MemoryScopeResolver:
	def __init__(self, namespace: str, scope_keys: list[str], include_session_scope: bool) -> None:
		self.namespace = namespace
		self.scope_keys = scope_keys
		self.include_session_scope = include_session_scope

	def scope_id(self, exec_ctx: "ExecutionContext | None") -> str:
		parts = [self.namespace]
		metadata = exec_ctx.state.metadata if exec_ctx else {}
		for key in self.scope_keys:
			value = metadata.get(key)
			if value:
				parts.append(f"{key}={value}")
		if self.include_session_scope and metadata.get("session_id"):
			parts.append(f"session_id={metadata['session_id']}")
		return "|".join(parts)

	def key_prefix(self, scope: str) -> str:
		return f"{_MEMORY_KEY_PREFIX}{scope}:"


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
		for key in await self.store.list_keys(self.scope_resolver.key_prefix(scope)):
			data = await self.store.get(key)
			if data:
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


class MemoryVectorIndex:
	def __init__(self, vector_store: Any, embedding_client: Any) -> None:
		self.vector_store = vector_store
		self.embedding_client = embedding_client

	async def embed_texts(self, texts: list[str]) -> list[list[float]]:
		if not self.embedding_client or not texts:
			return []
		try:
			vectors = await self.embedding_client.embed(texts)
		except Exception as e:
			logger.warning(f"[memory] embedding failed: {e}")
			return []
		return vectors if len(vectors) == len(texts) else []

	async def upsert(self, mem: dict, scope: str, service: MemoryService) -> dict:
		if not self.vector_store or not self.embedding_client:
			return mem
		vector_id = str(mem.get("metadata", {}).get("vector_id") or "")
		if vector_id:
			try:
				await self.vector_store.delete([vector_id])
			except Exception as e:
				logger.warning(f"[memory] vector delete failed before upsert: {e}")
		vectors = await self.embed_texts([str(mem.get("content", ""))])
		if not vectors:
			return mem
		metadata = {
			"scope": scope,
			"memory_id": mem["id"],
			"layer": mem.get("layer", ""),
			"fact_type": mem.get("fact_type", ""),
			"source": mem.get("source", ""),
		}
		try:
			ids = await self.vector_store.add([mem["content"]], vectors, [metadata])
		except Exception as e:
			logger.warning(f"[memory] vector upsert failed: {e}")
			return mem
		if ids:
			mem.setdefault("metadata", {})["vector_id"] = ids[0]
			item = service.store.get_item(mem["id"])
			if item:
				item.metadata["vector_id"] = ids[0]
		return mem

	async def delete(self, ids: list[str], memories: list[dict], repository: MemoryRepository,
					 scope: str) -> None:
		if not self.vector_store or not ids:
			return
		vector_ids: list[str] = []
		by_id = {item.get("id"): item for item in memories}
		for mem_id in ids:
			item = by_id.get(mem_id) or await repository.get_memory(scope, mem_id)
			vector_id = str((item or {}).get("metadata", {}).get("vector_id") or "")
			if vector_id:
				vector_ids.append(vector_id)
		if not vector_ids:
			return
		try:
			await self.vector_store.delete(vector_ids)
		except Exception as e:
			logger.warning(f"[memory] vector delete failed: {e}")

	async def retrieve(self, query: str, layer: str | None, top_k: int, service: MemoryService,
					   scope: str, lexical: list[Any]) -> list[Any]:
		if not query or not self.vector_store or not self.embedding_client:
			return lexical
		vectors = await self.embed_texts([query])
		if not vectors:
			return lexical
		vector_items = []
		try:
			raw_results = await self.vector_store.search(vectors[0], top_k=max(top_k * 3, top_k))
		except Exception as e:
			logger.warning(f"[memory] vector search failed: {e}")
			return lexical
		for row in raw_results:
			metadata = dict(row.get("metadata") or {})
			if metadata.get("scope") and metadata.get("scope") != scope:
				continue
			if layer and metadata.get("layer") != str(layer):
				continue
			mem_id = str(metadata.get("memory_id") or row.get("id") or "")
			item = service.store.get_item(mem_id)
			if item:
				vector_items.append(service._touch(item))
		merged = []
		seen: set[str] = set()
		for item in [*vector_items, *lexical]:
			if item.id in seen:
				continue
			merged.append(item)
			seen.add(item.id)
			if len(merged) >= top_k:
				break
		return merged


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
						await add_memory(item["content"], item.get("importance", 0.5), fact_type=item.get("type", "fact"), layer=layer, source="auto_extract", exec_ctx=exec_ctx)
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
		except Exception as e:
			await on_failure(e)


class MemoryToolHandlers:
	def __init__(self, plugin: "MemoryPlugin") -> None:
		self.plugin = plugin

	async def add(self, args: dict, context: dict):
		return await self.add_memory_result(
			content=args.get("content", ""),
			importance=args.get("importance", 0.7),
			layer=args.get("layer", MemoryLayer.SEMANTIC),
			fact_type=args.get("fact_type", "fact"),
			exec_ctx=context.get("exec_ctx") if isinstance(context, dict) else None,
		)

	async def add_fact(self, args: dict, context: dict):
		return await self.add_memory_result(
			content=args.get("content", ""),
			importance=args.get("importance", 0.7),
			layer=MemoryLayer.SEMANTIC,
			fact_type=args.get("fact_type", "fact"),
			exec_ctx=context.get("exec_ctx") if isinstance(context, dict) else None,
		)

	async def add_lesson(self, args: dict, context: dict):
		return await self.add_memory_result(
			content=args.get("content", ""),
			importance=args.get("importance", 0.9),
			layer=MemoryLayer.LESSON,
			fact_type="lesson",
			exec_ctx=context.get("exec_ctx") if isinstance(context, dict) else None,
		)

	async def add_memory_result(self, content: str, importance: float, layer: str, fact_type: str,
								exec_ctx: "ExecutionContext | None" = None):
		from axc_agent_engine.tools.tool_output import ToolOutput
		if not content:
			return ToolOutput.error("content 不能为空")
		try:
			layer_value = MemoryLayer(str(layer))
		except ValueError:
			return ToolOutput.error(f"invalid memory layer: {layer}")
		plugin = self.plugin
		async with plugin._lock:
			await plugin._load_memories(exec_ctx)
			sanitized, privacy = plugin._privacy_policy.sanitize(content)
			if privacy["rejected"]:
				return ToolOutput.error("memory rejected by sensitive content policy")
			if plugin._is_duplicate(sanitized):
				plugin._stats["duplicates"] += 1
				plugin._sync_metadata(exec_ctx)
				return ToolOutput.json_output({"status": "duplicate", "message": "记忆已存在"})
			conflict = plugin._find_conflict(sanitized, fact_type, str(layer_value))
			await plugin._add_memory(content, importance, fact_type=fact_type, layer=layer_value, exec_ctx=exec_ctx, metadata={"manual": True})
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
		plugin = self.plugin
		exec_ctx = context.get("exec_ctx") if isinstance(context, dict) else None
		query = str(args.get("query", ""))
		layer = args.get("layer")
		top_k = bounded_int(args.get("top_k", 5), 1, 50)
		async with plugin._lock:
			await plugin._load_memories(exec_ctx)
			items = await plugin._retrieve_memories(query, layer, top_k, exec_ctx)
			plugin._stats["searches"] += 1
			await plugin._persist_memories([item.id for item in items], exec_ctx)
			plugin._sync_metadata(exec_ctx)
		return ToolOutput.json_output(
			{"memories": [_public_memory(item.to_dict()) for item in items], "query": query},
			summary=f"找到 {len(items)} 条记忆",
		)

	async def list(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		plugin = self.plugin
		exec_ctx = context.get("exec_ctx") if isinstance(context, dict) else None
		layer = args.get("layer")
		limit = bounded_int(args.get("limit", 20), 1, 200)
		async with plugin._lock:
			await plugin._load_memories(exec_ctx)
			items = [item for item in plugin._service.items if not layer or item.layer == str(layer)]
			items.sort(key=lambda item: item.created_at, reverse=True)
			plugin._sync_metadata(exec_ctx)
		return ToolOutput.json_output(
			{"memories": [_public_memory(item.to_dict()) for item in items[:limit]], "count": len(items)},
			summary=f"列出 {min(len(items), limit)} 条记忆",
		)

	async def delete(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		plugin = self.plugin
		exec_ctx = context.get("exec_ctx") if isinstance(context, dict) else None
		mem_id = str(args.get("id", ""))
		if not mem_id:
			return ToolOutput.error("id 不能为空")
		async with plugin._lock:
			await plugin._load_memories(exec_ctx)
			deleted = plugin._service.store.delete_item(mem_id)
			if deleted:
				plugin._sync_memories_view()
				await plugin._delete_memories([mem_id], exec_ctx)
				plugin._stats["deleted"] += 1
				await plugin._audit_memory(exec_ctx, "deleted", mem_id, capability="memory_delete", risk_level="dangerous")
			plugin._sync_metadata(exec_ctx)
		return ToolOutput.json_output({"deleted": deleted, "id": mem_id}, summary="记忆已删除" if deleted else "记忆不存在")

	async def export(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		plugin = self.plugin
		exec_ctx = context.get("exec_ctx") if isinstance(context, dict) else None
		layer = args.get("layer")
		async with plugin._lock:
			await plugin._load_memories(exec_ctx)
			memories = [_public_memory(item) for item in plugin._memories if not layer or item.get("layer") == str(layer)]
			plugin._sync_metadata(exec_ctx)
		return ToolOutput.json_output({"memories": memories, "count": len(memories)}, summary=f"导出 {len(memories)} 条记忆")


class MemoryPlugin(BasePlugin):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
记忆系统 — 依赖 KVStore Protocol 持久化

	配置项：
	  context_budget: int = 2000          # 上下文注入字符预算
	  decay_half_life: int = 7            # 衰减半衰期（天）
	  dedup_threshold: float = 0.85       # 去重相似度阈值
	  min_content_length: int = 50        # 最小记忆内容长度
	"""
	name = "memory"
	display_name = "记忆系统"
	priority = 20
	version = "2.0.0"

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._context_budget = config.get("context_budget", 2000)
		self._decay_half_life = config.get("decay_half_life", 7)
		self._dedup_threshold = config.get("dedup_threshold", 0.85)
		self._min_content_length = config.get("min_content_length", 50)
		self._namespace = str(config.get("namespace", "default"))
		self._scope_keys = list(config.get("scope_keys", ["tenant_id", "user_id", "agent_name"]))
		self._include_session_scope = bool(config.get("session_scope", False))
		self._sensitive_policy = str(config.get("sensitive_policy", "redact"))
		self._sensitive_patterns = [re.compile(pattern) for pattern in config.get("sensitive_patterns", _DEFAULT_SENSITIVE_PATTERNS)]
		self._scope_resolver = MemoryScopeResolver(self._namespace, self._scope_keys, self._include_session_scope)
		self._privacy_policy = MemoryPrivacyPolicy(self._sensitive_policy, self._sensitive_patterns)
		self._max_memories = int(config.get("max_memories", 10000))
		self._ttl_days = int(config.get("ttl_days", 0) or 0)
		self._auto_extract = bool(config.get("auto_extract", True))
		self._plugin_ctx = plugin_ctx
		self._store = plugin_ctx.kv_store
		self._repository = MemoryRepository(self._store, self._scope_resolver)
		self._vector_resource = _resource_name(config.get("vector_store"), "memory_vector")
		self._vector_store = plugin_ctx.resources.get(self._vector_resource) if self._vector_resource else None
		self._embedding_config = dict(config.get("embedding") or {})
		self._embedding_client = self._init_embedding_client()
		self._vector_index = MemoryVectorIndex(self._vector_store, self._embedding_client)
		self._extraction_service = MemoryExtractionService(self._min_content_length)
		self._tool_handlers = MemoryToolHandlers(self)
		self._memories: list[dict] = []
		self._service = MemoryService(
			dedup_threshold=self._dedup_threshold,
			decay_half_life_days=self._decay_half_life,
		)
		self._loaded_scopes: set[str] = set()
		self._scope_cache: dict[str, list[dict]] = {}
		self._active_scope = self._scope_resolver.scope_id(None)
		self._lock = asyncio.Lock()
		self._background_tasks: set[asyncio.Task] = set()
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

	async def _load_memories(self, exec_ctx: "ExecutionContext | None" = None) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
从 KVStore 加载记忆"""
		scope = self._scope_resolver.scope_id(exec_ctx)
		self._active_scope = scope
		if scope in self._scope_cache:
			self._service.load(self._scope_cache[scope])
			self._sync_memories_view()
			self._sync_metadata(exec_ctx)
			return
		if not self._store:
			self._service.load([])
			self._sync_memories_view()
			self._scope_cache[scope] = list(self._memories)
			self._loaded_scopes.add(scope)
			return
		try:
			records = await self._repository.load_scope(scope)
			self._service.load(records)
			self._sync_memories_view()
			self._loaded_scopes.add(scope)
			self._stats["loaded"] += len(records)
			self._sync_metadata(exec_ctx)
			if self._memories:
				logger.info(f"[memory] Loaded {len(self._memories)} memories for scope={scope}")
		except Exception as e:
			logger.warning(f"[memory] Failed to load memories: {e}")

	async def on_execution_start(self, exec_ctx: "ExecutionContext") -> None:
		"""English: This documentation describes the related engine component behavior.
中文：执行开始时加载记忆"""
		async with self._lock:
			await self._load_memories(exec_ctx)

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str = "") -> str:
		self._active_scope = self._scope_resolver.scope_id(exec_ctx)
		before = _access_snapshot(self._service.dump())
		context = self._service.build_context(topic, budget_chars=self._context_budget)
		self._sync_memories_view()
		touched = _changed_access_ids(before, self._memories)
		if touched:
			self._schedule_persist(touched, exec_ctx)
		self._sync_metadata(exec_ctx)
		return context

	def get_tools(self) -> list[ToolDefinition]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
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
						"layer": {"type": "string", "description": "记忆层级", "enum": [item.value for item in MemoryLayer], "default": MemoryLayer.SEMANTIC.value},
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
				parameters={"type": "object", "properties": {
					"query": {"type": "string", "description": "搜索主题"},
					"layer": {"type": "string", "enum": [item.value for item in MemoryLayer]},
					"top_k": {"type": "integer", "default": 5},
				}},
				is_read_only=True,
				capability="memory_read",
				risk_level="safe",
				execute=self._tool_memory_search,
			),
			ToolDefinition(
				name="memory_list",
				description="列出当前作用域内的记忆",
				parameters={"type": "object", "properties": {
					"layer": {"type": "string", "enum": [item.value for item in MemoryLayer]},
					"limit": {"type": "integer", "default": 20},
				}},
				is_read_only=True,
				capability="memory_read",
				risk_level="safe",
				execute=self._tool_memory_list,
			),
			ToolDefinition(
				name="memory_delete",
				description="删除当前作用域内的一条记忆",
				parameters={"type": "object", "properties": {
					"id": {"type": "string", "description": "记忆 ID"},
				}, "required": ["id"]},
				is_read_only=False,
				capability="memory_delete",
				risk_level="dangerous",
				execute=self._tool_memory_delete,
			),
			ToolDefinition(
				name="memory_export",
				description="导出当前作用域内的记忆",
				parameters={"type": "object", "properties": {
					"layer": {"type": "string", "enum": [item.value for item in MemoryLayer]},
				}},
				is_read_only=True,
				capability="memory_read",
				risk_level="safe",
				execute=self._tool_memory_export,
			),
		]

	async def on_round_end(self, exec_ctx: "ExecutionContext", user_message: str,
						   assistant_message: str, tool_calls: list[dict]) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
调 utility_llm 从对话中提取事实"""
		if not self._auto_extract or (not user_message and not assistant_message):
			return
		conversation = ""
		if user_message:
			conversation += f"用户: {user_message}\n"
		if assistant_message:
			conversation += f"助手: {assistant_message}\n"
		utility_llm = self._plugin_ctx.utility_llm if self._plugin_ctx else None
		async with self._lock:
			if utility_llm:
				await self._extract_with_llm(conversation, utility_llm, exec_ctx)
			else:
				if user_message and len(user_message) >= self._min_content_length:
					await self._add_memory(user_message, 0.5, exec_ctx=exec_ctx)

	async def on_execution_end(self, exec_ctx: "ExecutionContext", result: str, error: str) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：执行结束时做记忆衰减 — 移除过期低分记忆"""
		async with self._lock:
			await self._execution_end_locked(exec_ctx)
		await self._flush_background_tasks()

	async def _execution_end_locked(self, exec_ctx: "ExecutionContext") -> None:
		self._sync_service_from_view()
		if not self._memories:
			return
		expired_ids = self._service.remove_decayed(threshold=0.05)
		if self._ttl_days > 0:
			expired_ids = list(set(expired_ids) | set(self._ttl_expired_ids()))
		if expired_ids:
			self._sync_memories_view()
			await self._delete_memories(expired_ids, exec_ctx)
			self._stats["deleted"] += len(expired_ids)
			self._sync_metadata(exec_ctx)
			logger.info(f"[memory] Decay removed {len(expired_ids)} memories")

	async def _extract_with_llm(self, conversation: str, llm: Any, exec_ctx: "ExecutionContext | None" = None) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
调 LLM 提取事实"""
		async def on_failure(e: Exception) -> None:
			self._stats["extraction_failures"] += 1
			self._sync_metadata(exec_ctx)
			logger.warning(f"[memory] LLM fact extraction failed: {e}")
		await self._extraction_service.extract_with_llm(
			conversation,
			llm,
			exec_ctx,
			self._add_memory,
			self._is_duplicate,
			self._valid_extracted_fact,
			on_failure,
		)

	async def _add_memory(self, content: str, importance: float, source: str = "",
						  fact_type: str = "fact", layer: str = MemoryLayer.SEMANTIC,
						  exec_ctx: "ExecutionContext | None" = None,
						  metadata: dict[str, Any] | None = None) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：添加一条记忆"""
		sanitized, privacy = self._privacy_policy.sanitize(content)
		if privacy["rejected"]:
			self._stats["rejected_sensitive"] += 1
			self._sync_metadata(exec_ctx)
			return
		if privacy["redacted"]:
			self._stats["redacted"] += 1
		conflict = self._find_conflict(sanitized, fact_type, str(layer))
		item_metadata = dict(metadata or {})
		item_metadata.update({
			"scope": self._scope_resolver.scope_id(exec_ctx),
			"privacy": privacy,
			"conflict_with": conflict["id"] if conflict else "",
		})
		if conflict:
			self._stats["conflicts"] += 1
		item = self._service.add(sanitized, layer=layer, fact_type=fact_type, importance=importance, source=source, metadata=item_metadata)
		mem = item.to_dict()
		self._sync_memories_view()
		removed_ids = self._enforce_capacity()
		evicted = mem["id"] in set(removed_ids)
		if not evicted:
			mem = await self._upsert_vector(mem, exec_ctx)
		if self._store:
			if removed_ids:
				await self._delete_memories(removed_ids, exec_ctx)
			if not evicted:
				await self._repository.save_memory(self._scope_resolver.scope_id(exec_ctx), mem)
		if not evicted:
			self._stats["added"] += 1
			await self._audit_memory(exec_ctx, "added", mem["id"], capability="memory_write", risk_level="moderate")
		self._sync_metadata(exec_ctx)

	def _is_duplicate(self, content: str) -> bool:
		"""English: This documentation describes the related engine component behavior.
中文：简单去重：字符级相似度"""
		self._sync_service_from_view()
		return self._service.find_duplicate(content) is not None

	def _compute_score(self, mem: dict, topic: str) -> float:
		"""English: This documentation describes the related engine component behavior.
中文：计算记忆得分（相关性 × 衰减后重要性）"""
		importance = mem.get("importance", 0.5)
		created_at = mem.get("created_at", "")
		if created_at:
			try:
				created = datetime.fromisoformat(created_at)
				now = datetime.now(timezone.utc)
				days = (now - created).total_seconds() / 86400
				decay = math.pow(0.5, days / self._decay_half_life)
				importance *= decay
			except (ValueError, TypeError):
				pass
		relevance = 0.5
		if topic:
			content = mem.get("content", "").lower()
			topic_lower = topic.lower()
			words = topic_lower.split()
			matched = sum(1 for w in words if w in content)
			if matched > 0:
				relevance = min(1.0, 0.5 + matched * 0.2)
		return importance * relevance

	def _sync_memories_view(self) -> None:
		self._memories = self._service.dump()
		if hasattr(self, "_scope_cache"):
			self._scope_cache[self._active_scope] = list(self._memories)

	def _sync_service_from_view(self) -> None:
		current = self._service.dump()
		if self._memories != current:
			self._service.load(self._memories)

	async def _delete_memories(self, ids: list[str], exec_ctx: "ExecutionContext | None" = None) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：批量删除记忆"""
		await self._delete_memory_vectors(ids, exec_ctx)
		await self._repository.delete_memories(self._scope_resolver.scope_id(exec_ctx), ids)

	async def _tool_memory_add(self, args: dict, context: dict):
		"""memory_add 工具实现"""
		return await self._tool_handlers.add(args, context)

	async def _tool_memory_add_fact(self, args: dict, context: dict):
		return await self._tool_handlers.add_fact(args, context)

	async def _tool_memory_add_lesson(self, args: dict, context: dict):
		return await self._tool_handlers.add_lesson(args, context)

	async def _add_memory_tool_result(self, content: str, importance: float, layer: str, fact_type: str,
									  exec_ctx: "ExecutionContext | None" = None):
		return await self._tool_handlers.add_memory_result(content, importance, layer, fact_type, exec_ctx)

	async def _tool_memory_search(self, args: dict, context: dict):
		return await self._tool_handlers.search(args, context)

	async def _tool_memory_list(self, args: dict, context: dict):
		return await self._tool_handlers.list(args, context)

	async def _tool_memory_delete(self, args: dict, context: dict):
		return await self._tool_handlers.delete(args, context)

	async def _tool_memory_export(self, args: dict, context: dict):
		return await self._tool_handlers.export(args, context)

	def _find_conflict(self, content: str, fact_type: str, layer: str) -> dict | None:
		if not fact_type:
			return None
		negative = _is_negative_fact(content)
		for item in self._memories:
			if item.get("layer") != layer or item.get("fact_type") != fact_type:
				continue
			if _is_negative_fact(item.get("content", "")) != negative and char_similarity(_normalize_fact_text(content), _normalize_fact_text(item.get("content", ""))) >= 0.4:
				return item
		return None

	def _ttl_expired_ids(self) -> list[str]:
		if self._ttl_days <= 0:
			return []
		cutoff = datetime.now(timezone.utc) - timedelta(days=self._ttl_days)
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
			self._sync_memories_view()
		return expired

	def _enforce_capacity(self) -> list[str]:
		if self._max_memories <= 0 or len(self._service.items) <= self._max_memories:
			return []
		items = sorted(self._service.items, key=lambda item: (item.importance, item.decay_score, item.created_at))
		remove_ids = {item.id for item in items[:len(items) - self._max_memories]}
		self._service.items = [item for item in self._service.items if item.id not in remove_ids]
		self._sync_memories_view()
		return list(remove_ids)

	def _init_embedding_client(self) -> Any:
		if not self._vector_store:
			return None
		mode = str(self._embedding_config.get("mode", "")).lower()
		if self._embedding_config.get("base_url") and self._embedding_config.get("model"):
			return OpenAICompatibleEmbeddingClient(
				str(self._embedding_config["base_url"]),
				str(self._embedding_config["model"]),
				str(self._embedding_config.get("api_key", "")),
				int(self._embedding_config.get("timeout", 30)),
			)
		if mode in ("", "hash"):
			return HashEmbeddingClient(int(self._embedding_config.get("dimensions", 256)))
		return None

	async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
		return await self._vector_index.embed_texts(texts)

	async def _upsert_vector(self, mem: dict, exec_ctx: "ExecutionContext | None" = None) -> dict:
		mem = await self._vector_index.upsert(mem, self._scope_resolver.scope_id(exec_ctx), self._service)
		self._sync_memories_view()
		return mem

	async def _delete_memory_vectors(self, ids: list[str], exec_ctx: "ExecutionContext | None" = None) -> None:
		await self._vector_index.delete(ids, self._memories, self._repository, self._scope_resolver.scope_id(exec_ctx))

	async def _retrieve_memories(self, query: str, layer: str | None, top_k: int,
								 exec_ctx: "ExecutionContext | None" = None):
		lexical = self._service.retrieve(query, layer=layer, top_k=top_k)
		merged = await self._vector_index.retrieve(query, layer, top_k, self._service, self._scope_resolver.scope_id(exec_ctx), lexical)
		self._sync_memories_view()
		return merged

	async def _persist_memories(self, ids: list[str], exec_ctx: "ExecutionContext | None" = None) -> None:
		await self._repository.persist_memories(self._scope_resolver.scope_id(exec_ctx), self._service.dump(), ids)

	def _schedule_persist(self, ids: list[str], exec_ctx: "ExecutionContext | None" = None) -> None:
		try:
			loop = asyncio.get_running_loop()
		except RuntimeError:
			return
		task = loop.create_task(self._persist_memories(ids, exec_ctx))
		self._background_tasks.add(task)
		task.add_done_callback(self._background_tasks.discard)

	async def _flush_background_tasks(self) -> None:
		if not self._background_tasks:
			return
		tasks = list(self._background_tasks)
		self._background_tasks.clear()
		await asyncio.gather(*tasks, return_exceptions=True)

	async def _audit_memory(self, exec_ctx: "ExecutionContext | None", action: str, memory_id: str,
							capability: str, risk_level: str) -> None:
		if not exec_ctx or not exec_ctx.services.audit_sink:
			return
		from axc_agent_engine.observability.audit import AuditEvent
		metadata = exec_ctx.state.metadata
		await exec_ctx.services.audit_sink.record(AuditEvent(
			type=f"memory_{action}",
			actor=str(metadata.get("user_id") or metadata.get("agent_name") or ""),
			session_id=str(metadata.get("session_id") or ""),
			tool_name="memory",
			capability=capability,
			risk_level=risk_level,
			metadata={"memory_id": memory_id, "scope": self._scope_resolver.scope_id(exec_ctx)},
		))

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
			"scope": self._scope_resolver.scope_id(exec_ctx),
			"count": len(self._memories),
			"stats": dict(self._stats),
			"layers": _layer_counts(self._memories),
		}


def _char_similarity(a: str, b: str) -> float:
	"""Bigram Jaccard 相似度，比字符级去重更适合语义去重。"""
	return char_similarity(a, b)


def _resource_name(value: Any, default: str) -> str:
	if value is None or value is True:
		return default
	if value is False:
		return ""
	return str(value)


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


def _layer_counts(memories: list[dict]) -> dict[str, int]:
	counts: dict[str, int] = {}
	for item in memories:
		layer = str(item.get("layer", ""))
		counts[layer] = counts.get(layer, 0) + 1
	return counts


def _is_negative_fact(content: str) -> bool:
	text = str(content).lower()
	return any(marker in text for marker in (" not ", " never ", " dislike", " don't ", "doesn't", "不", "没有", "讨厌"))


def _normalize_fact_text(content: str) -> str:
	text = str(content).lower()
	for marker in (" not ", " never ", " dislike", " don't ", "doesn't", "不", "没有", "讨厌"):
		text = text.replace(marker, " ")
	return " ".join(text.split())
