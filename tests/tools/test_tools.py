"""Tests for tools module — utils, registry, executor, orchestrator, decorator."""
import asyncio
import pytest

from axc_agent_engine.tools.utils import parse_tool_calls, parse_arguments
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.name_mapping import ToolNameMapper, ToolNameMappingConfig, sanitize_tool_name
from axc_agent_engine.tools.executor import execute_tool, is_retryable_error
from axc_agent_engine.tools.orchestrator import partition_tool_calls, execute_tool_calls
from axc_agent_engine.tools.decorator import tool
from axc_agent_engine.tools.tool_output import ToolOutput
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.core.context import ExecutionContext, ExecutionConfig


class TestParseToolCalls:
	def test_basic(self):
		raw = [{"id": "c1", "function": {"name": "echo", "arguments": '{"text": "hi"}'}}]
		result = parse_tool_calls(raw)
		assert len(result) == 1
		assert result[0]["id"] == "c1"
		assert result[0]["name"] == "echo"
		assert result[0]["arguments"] == {"text": "hi"}

	def test_empty_arguments(self):
		raw = [{"id": "c1", "function": {"name": "test", "arguments": ""}}]
		result = parse_tool_calls(raw)
		assert result[0]["arguments"] == {}

	def test_invalid_json(self):
		raw = [{"id": "c1", "function": {"name": "test", "arguments": "not json"}}]
		result = parse_tool_calls(raw)
		assert result[0]["arguments"] == {"_raw": "not json"}

	def test_multiple_calls(self):
		raw = [
			{"id": "c1", "function": {"name": "a", "arguments": "{}"}},
			{"id": "c2", "function": {"name": "b", "arguments": '{"x": 1}'}},
		]
		result = parse_tool_calls(raw)
		assert len(result) == 2
		assert result[1]["name"] == "b"
		assert result[1]["arguments"] == {"x": 1}

	def test_missing_fields(self):
		raw = [{"function": {}}]
		result = parse_tool_calls(raw)
		assert result[0]["id"] == ""
		assert result[0]["name"] == ""


class TestParseArguments:
	def test_valid_json(self):
		assert parse_arguments('{"a": 1}') == {"a": 1}

	def test_empty_string(self):
		assert parse_arguments("") == {}

	def test_invalid_json(self):
		assert parse_arguments("bad") == {"_raw": "bad"}

	def test_none_like(self):
		assert parse_arguments("") == {}


class TestToolRegistry:
	def test_register_and_get(self):
		reg = ToolRegistry()
		td = ToolDefinition(name="test", description="desc")
		reg.register(td)
		assert reg.get("test") is td
		assert reg.count == 1

	def test_register_rejects_dict(self):
		reg = ToolRegistry()
		with pytest.raises(TypeError, match="ToolDefinition"):
			reg.register({"name": "test", "description": "desc", "is_read_only": True})

	def test_register_many(self):
		reg = ToolRegistry()
		reg.register_many([
			ToolDefinition(name="a"), ToolDefinition(name="b"),
		])
		assert reg.count == 2

	def test_get_nonexistent(self):
		reg = ToolRegistry()
		assert reg.get("nope") is None

	def test_has(self):
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="x"))
		assert reg.has("x") is True
		assert reg.has("y") is False

	def test_clear(self):
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="x"))
		reg.clear()
		assert reg.count == 0

	def test_openai_schemas_excludes_deferred(self):
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="normal", description="n"))
		reg.register(ToolDefinition(name="deferred", description="d", deferred=True))
		schemas = reg.get_openai_schemas()
		assert len(schemas) == 1
		assert schemas[0]["function"]["name"] == "normal"

	def test_openai_schemas_sorted(self):
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="z_tool"))
		reg.register(ToolDefinition(name="a_tool"))
		schemas = reg.get_openai_schemas()
		assert schemas[0]["function"]["name"] == "a_tool"

	def test_skip_empty_name(self):
		reg = ToolRegistry()
		reg.register(ToolDefinition(name=""))
		assert reg.count == 0

	def test_openai_schema_uses_model_safe_alias(self):
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="mcp.github.search_repo"))
		schemas = reg.get_openai_schemas()
		assert schemas[0]["function"]["name"] == "mcp_github_search_repo"
		assert reg.resolve_name("mcp_github_search_repo") == "mcp.github.search_repo"

	def test_alias_collision_gets_hash_suffix(self):
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="a.b"))
		reg.register(ToolDefinition(name="a/b"))
		names = [s["function"]["name"] for s in reg.get_openai_schemas()]
		assert len(set(names)) == 2
		assert "a_b" in names

	def test_late_registration_records_source_and_schema_version(self):
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="initial"))
		reg.freeze()
		reg.register_late(ToolDefinition(name="late"), plugin_name="mcp", reason="discovery")
		log = reg.registration_log()
		assert reg.schema_version == 2
		assert log[-1]["name"] == "late"
		assert log[-1]["source"] == "mcp"
		assert log[-1]["reason"] == "discovery"
		assert log[-1]["frozen"] is True


class TestToolNameMapper:
	def test_sanitize_preserves_valid_name(self):
		assert sanitize_tool_name("valid_name-1") == "valid_name-1"

	def test_sanitize_replaces_invalid_chars(self):
		assert sanitize_tool_name("mcp.github/search") == "mcp_github_search"

	def test_custom_replacement_and_lowercase(self):
		config = ToolNameMappingConfig(replacement="__", case="lower")
		assert sanitize_tool_name("MCP.GitHub/Search", config) == "mcp__github__search"

	def test_mapper_roundtrip(self):
		mapper = ToolNameMapper()
		alias = mapper.encode("mcp.github.search")
		assert alias == "mcp_github_search"
		assert mapper.decode(alias) == "mcp.github.search"


class TestToolExecutor:
	@pytest.mark.asyncio
	async def test_execute_success(self):
		async def fn(args, ctx):
			return ToolOutput.text(f"got {args['x']}")
		td = ToolDefinition(name="test", execute=fn)
		result = await execute_tool(td, {"x": "hello"}, "id1")
		assert result.success is True
		assert result.output.content == "got hello"
		assert result.duration_ms >= 0

	@pytest.mark.asyncio
	async def test_execute_no_execute_fn(self):
		td = ToolDefinition(name="test")
		result = await execute_tool(td, {}, "id1")
		assert result.success is False
		assert "execute" in result.error.lower()

	@pytest.mark.asyncio
	async def test_execute_timeout(self):
		async def slow(args, ctx):
			await asyncio.sleep(10)
			return ToolOutput.text("done")
		td = ToolDefinition(name="slow", execute=slow, timeout=0.1)
		result = await execute_tool(td, {}, "id1")
		assert result.success is False
		assert "timeout" in result.error.lower()

	@pytest.mark.asyncio
	async def test_execute_exception(self):
		async def bad(args, ctx):
			raise ValueError("oops")
		td = ToolDefinition(name="bad", execute=bad)
		result = await execute_tool(td, {}, "id1")
		assert result.success is False
		assert "oops" in result.error

	@pytest.mark.asyncio
	async def test_non_tooloutput_rejected(self):
		"""Tools returning non-ToolOutput are rejected."""
		async def invalid_tool(args, ctx):
			return "plain string"
		td = ToolDefinition(name="invalid_tool", execute=invalid_tool)
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			await execute_tool(td, {}, "id1")

	@pytest.mark.asyncio
	async def test_retry_on_retryable_error(self):
		call_count = 0
		async def flaky(args, ctx):
			nonlocal call_count
			call_count += 1
			if call_count < 2:
				raise ConnectionError("connection refused")
			return ToolOutput.text("ok")
		td = ToolDefinition(name="flaky", execute=flaky, is_read_only=True)
		result = await execute_tool(td, {}, "id1")
		assert result.success is True
		assert call_count == 2

	@pytest.mark.asyncio
	async def test_no_retry_on_non_retryable(self):
		call_count = 0
		async def perm_fail(args, ctx):
			nonlocal call_count
			call_count += 1
			raise PermissionError("forbidden")
		td = ToolDefinition(name="perm", execute=perm_fail, is_read_only=True)
		result = await execute_tool(td, {}, "id1")
		assert result.success is False
		assert call_count == 1

	@pytest.mark.asyncio
	async def test_error_tooloutput(self):
		"""ToolOutput with is_error=True is treated as failure."""
		async def err_tool(args, ctx):
			return ToolOutput.error("something went wrong")
		td = ToolDefinition(name="err", execute=err_tool)
		result = await execute_tool(td, {}, "id1")
		assert result.success is False
		assert "something went wrong" in result.error

	def test_is_retryable_error(self):
		assert is_retryable_error("connection refused") is True
		assert is_retryable_error("timeout occurred") is True
		assert is_retryable_error("429 too many requests") is True
		assert is_retryable_error("403 forbidden") is False
		assert is_retryable_error("invalid parameter") is False
		assert is_retryable_error("random error") is False


class TestOrchestrator:
	def test_partition_read_only_concurrent(self, tool_registry):
		calls = [
			{"name": "echo", "arguments": {}, "id": "1"},
			{"name": "echo", "arguments": {}, "id": "2"},
		]
		batches = partition_tool_calls(calls, tool_registry)
		assert len(batches) == 1
		assert batches[0]["concurrent"] is True
		assert len(batches[0]["calls"]) == 2

	def test_partition_write_serial(self, tool_registry):
		calls = [
			{"name": "write_file", "arguments": {}, "id": "1"},
			{"name": "write_file", "arguments": {}, "id": "2"},
		]
		batches = partition_tool_calls(calls, tool_registry)
		assert len(batches) == 2
		assert all(b["concurrent"] is False for b in batches)

	def test_partition_mixed(self, tool_registry):
		calls = [
			{"name": "echo", "arguments": {}, "id": "1"},
			{"name": "echo", "arguments": {}, "id": "2"},
			{"name": "write_file", "arguments": {}, "id": "3"},
			{"name": "echo", "arguments": {}, "id": "4"},
		]
		batches = partition_tool_calls(calls, tool_registry)
		assert len(batches) == 3
		assert batches[0]["concurrent"] is True
		assert batches[1]["concurrent"] is False
		assert batches[2]["concurrent"] is True

	@pytest.mark.asyncio
	async def test_execute_tool_calls_basic(self, tool_registry):
		calls = [{"name": "echo", "arguments": {"text": "hi"}, "id": "1"}]
		ctx = ExecutionContext(config=ExecutionConfig(stream=True))
		results = await execute_tool_calls(calls, tool_registry, [], ctx)
		assert len(results) == 1
		assert results[0].success is True
		assert "hi" in results[0].compact_view()

	@pytest.mark.asyncio
	async def test_execute_unknown_tool(self, tool_registry):
		calls = [{"name": "nonexistent", "arguments": {}, "id": "1"}]
		ctx = ExecutionContext(config=ExecutionConfig(stream=True))
		results = await execute_tool_calls(calls, tool_registry, [], ctx)
		assert results[0].success is False
		assert "Unknown tool" in results[0].error

	@pytest.mark.asyncio
	async def test_plugin_rejection(self, tool_registry):
		class RejectPlugin:
			name = "reject"
			async def pre_tool_call(self, ctx, name, args):
				return False, args
			async def post_tool_call(self, ctx, name, args, result, dur):
				return result
		calls = [{"name": "echo", "arguments": {"text": "hi"}, "id": "1"}]
		ctx = ExecutionContext(config=ExecutionConfig(stream=True))
		results = await execute_tool_calls(calls, tool_registry, [RejectPlugin()], ctx)
		assert results[0].success is False
		assert "rejected" in results[0].error.lower()


class TestToolDecorator:
	@pytest.mark.asyncio
	async def test_basic_decorator(self):
		@tool(name="greet", description="Greet someone")
		async def greet(name: str, greeting: str = "Hello") -> ToolOutput:
			return ToolOutput.text(f"{greeting}, {name}!")

		td = greet.tool_definition
		assert td.name == "greet"
		assert td.description == "Greet someone"
		assert "name" in td.parameters["properties"]
		assert "required" in td.parameters
		assert "name" in td.parameters["required"]
		assert "greeting" not in td.parameters["required"]

	@pytest.mark.asyncio
	async def test_decorator_execution(self):
		@tool(name="add")
		async def add(a: int, b: int) -> ToolOutput:
			return ToolOutput.text(str(a + b))

		td = add.tool_definition
		result = await td.execute({"a": 3, "b": 4}, {})
		assert isinstance(result, ToolOutput)
		assert result.content == "7"

	@pytest.mark.asyncio
	async def test_decorator_default_name(self):
		@tool()
		async def my_func() -> ToolOutput:
			"""Does something."""
			return ToolOutput.text("ok")

		td = my_func.tool_definition
		assert td.name == "my_func"
		assert td.description == "Does something."

	def test_decorator_rejects_non_tooloutput_annotation(self):
		"""@tool rejects functions annotated with non-ToolOutput return type."""
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			@tool(name="bad")
			async def bad_tool(x: str) -> str:
				return x
