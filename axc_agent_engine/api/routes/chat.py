"""OpenAI Chat Completions compatible API subset."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from axc_agent_engine.api.deps import EngineState
from axc_agent_engine.core.events import EventType

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
	model_config = ConfigDict(extra="allow")

	role: str
	content: str | list[dict[str, Any]]


class ChatRequest(BaseModel):
	"""OpenAI Chat Completions compatible 请求。"""
	model_config = ConfigDict(extra="allow")

	model: str = ""
	agent: str = ""
	messages: list[ChatMessage]
	stream: bool = False
	session_id: str = ""
	temperature: float | None = None
	max_tokens: int | None = None
	top_p: float | None = None
	stop: str | list[str] | None = None
	presence_penalty: float | None = None
	frequency_penalty: float | None = None
	seed: int | None = None
	user: str | None = None
	n: int | None = None
	tools: list[dict[str, Any]] | None = None
	tool_choice: str | dict[str, Any] | None = None
	response_format: dict[str, Any] | None = None
	stream_options: dict[str, Any] | None = None


SUPPORTED_CHAT_PARAMETERS = frozenset({
	"model",
	"agent",
	"messages",
	"stream",
	"session_id",
	"temperature",
	"max_tokens",
	"top_p",
	"stop",
	"presence_penalty",
	"frequency_penalty",
	"seed",
	"user",
	"response_format",
	"stream_options",
})
UNSUPPORTED_CHAT_PARAMETERS = frozenset({
	"n",
	"tools",
	"tool_choice",
})
SUPPORTED_STREAM_OPTIONS = frozenset({"include_usage"})


class ChatOptionResolver:
	"""Extracts provider-facing options from the OpenAI-compatible request."""

	_OPTION_FIELDS = (
		"temperature",
		"max_tokens",
		"top_p",
		"stop",
		"presence_penalty",
		"frequency_penalty",
		"seed",
		"user",
		"response_format",
	)

	def llm_options(self, request: ChatRequest) -> dict[str, Any]:
		return {
			field: value
			for field in self._OPTION_FIELDS
			if (value := getattr(request, field)) is not None
		}

	def agent_name(self, request: ChatRequest) -> str:
		return request.agent or request.model

	def stream_include_usage(self, request: ChatRequest) -> bool:
		return bool((request.stream_options or {}).get("include_usage", False))


class ChatSubsetValidator:
	"""Owns the explicit compatibility boundary for /v1/chat/completions."""

	def validate(self, request: ChatRequest) -> JSONResponse | None:
		extra_keys = sorted((request.model_extra or {}).keys())
		if extra_keys:
			return _unsupported_parameter_response(extra_keys[0])
		if request.n is not None and request.n > 1:
			return _unsupported_parameter_response("n", "暂不支持 n > 1")
		if request.tools is not None:
			return _unsupported_parameter_response("tools", "暂不支持请求级 tools；请在 Agent YAML 中定义工具")
		if request.tool_choice is not None:
			return _unsupported_parameter_response("tool_choice", "暂不支持 tool_choice")
		if request.stream_options:
			unsupported = sorted(set(request.stream_options) - SUPPORTED_STREAM_OPTIONS)
			if unsupported:
				return _unsupported_parameter_response(f"stream_options.{unsupported[0]}")
			if not request.stream:
				return _unsupported_parameter_response("stream_options", "stream_options 仅在 stream=true 时支持")
		return None


class ChatResponsePresenter:
	"""Formats OpenAI-compatible response envelopes and SSE chunks."""

	def completion(
		self,
		request: ChatRequest,
		content: str,
		usage: dict[str, int],
		finish_reason: str = "stop",
	) -> dict[str, Any]:
		return {
			"id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
			"object": "chat.completion",
			"created": int(time.time()),
			"model": _CHAT_OPTIONS.agent_name(request),
			"choices": [{
				"index": 0,
				"message": {"role": "assistant", "content": content},
				"finish_reason": finish_reason,
			}],
			"usage": usage,
		}

	def chunk(
		self,
		resp_id: str,
		model: str,
		choices: list[dict[str, Any]],
		usage: dict[str, int] | None = None,
	) -> dict[str, Any]:
		chunk: dict[str, Any] = {
			"id": resp_id,
			"object": "chat.completion.chunk",
			"created": int(time.time()),
			"model": model,
			"choices": choices,
		}
		if usage is not None:
			chunk["usage"] = usage
		return chunk

	def tool_call_delta(self, index: int, call_id: str, name: str, arguments: str) -> dict[str, Any]:
		return {
			"index": index,
			"id": call_id,
			"type": "function",
			"function": {"name": name, "arguments": arguments},
		}

	def sse(self, data: dict[str, Any]) -> str:
		return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


_CHAT_OPTIONS = ChatOptionResolver()
_CHAT_VALIDATOR = ChatSubsetValidator()
_CHAT_PRESENTER = ChatResponsePresenter()


def _error_response(
	status: int,
	message: str,
	error_type: str = "invalid_request_error",
	code: str | None = None,
) -> JSONResponse:
	"""返回 OpenAI-style error object。"""
	body: dict[str, Any] = {"error": {"message": message, "type": error_type, "param": None}}
	if code:
		body["error"]["code"] = code
	return JSONResponse(status_code=status, content=body)


def _unsupported_parameter_response(param: str, detail: str = "") -> JSONResponse:
	message = detail or f"Unsupported parameter: {param}"
	body = {"error": {
		"message": message,
		"type": "invalid_request_error",
		"param": param,
		"code": "unsupported_parameter",
	}}
	return JSONResponse(status_code=400, content=body)


def _capabilities() -> dict[str, Any]:
	"""返回当前 API 兼容子集，便于 SDK/调用方做能力探测。"""
	return {
		"object": "axc.api_capabilities",
		"openai_compatibility": {
			"api": "chat_completions",
			"level": "subset",
			"routes": ["/v1/chat/completions", "/v1/agents", "/v1/capabilities"],
		},
		"chat_completions": {
			"supported_parameters": sorted(SUPPORTED_CHAT_PARAMETERS),
			"unsupported_parameters": sorted(UNSUPPORTED_CHAT_PARAMETERS),
			"supported_stream_options": sorted(SUPPORTED_STREAM_OPTIONS),
			"request_level_tools": False,
			"tool_choice": False,
			"n_values": [1],
			"streaming": True,
			"sse_done_marker": True,
			"usage_in_stream": "stream_options.include_usage",
		},
		"agent_extensions": {
			"agent_field": True,
			"session_id": True,
			"tools_source": "Agent YAML and plugins",
		},
	}


def create_chat_router(state: EngineState) -> APIRouter:
	router = APIRouter(tags=["chat"])

	@router.post("/v1/chat/completions")
	async def chat_completions(request: ChatRequest):
		agent_name = _CHAT_OPTIONS.agent_name(request)
		if not agent_name:
			return _error_response(400, "需要指定 agent 或 model 字段")
		unsupported = _CHAT_VALIDATOR.validate(request)
		if unsupported:
			return unsupported
		agent = state.get_agent(agent_name)
		if not agent:
			return _error_response(404, f"Agent '{agent_name}' 不存在", error_type="not_found_error")
		messages = [m.model_dump() for m in request.messages]
		if not any(m["role"] == "user" for m in messages):
			return _error_response(400, "messages 中没有 user 消息")
		llm_opts = _CHAT_OPTIONS.llm_options(request)
		if request.stream:
			return StreamingResponse(
				_stream_response(agent, messages, request, llm_opts),
				media_type="text/event-stream",
			)
		return await _sync_response(agent, messages, request, llm_opts)

	@router.get("/v1/agents")
	async def list_agents():
		return {"agents": state.list_agents()}

	@router.get("/v1/capabilities")
	async def capabilities():
		return _capabilities()

	return router


async def _sync_response(
	agent: Any,
	messages: list[dict],
	request: ChatRequest,
	llm_options: dict,
) -> dict | JSONResponse:
	"""非流式响应：消费事件流并收集 usage。"""
	content = ""
	usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
	finish_reason = "stop"
	async for event in agent.stream_with_messages(messages, session_id=request.session_id, llm_options=llm_options):
		if event.type == EventType.COST_UPDATE:
			input_tokens = event.metadata.get("input_tokens", 0)
			output_tokens = event.metadata.get("output_tokens", 0)
			usage = _usage(input_tokens, output_tokens)
		elif event.type == EventType.DONE:
			content = event.content
		elif event.type == EventType.ERROR:
			return _error_response(500, event.content, "server_error")
	return _CHAT_PRESENTER.completion(request, content, usage, finish_reason)


def _usage(input_tokens: int, output_tokens: int) -> dict[str, int]:
	return {
		"prompt_tokens": input_tokens,
		"completion_tokens": output_tokens,
		"total_tokens": input_tokens + output_tokens,
	}


async def _stream_response(agent: Any, messages: list[dict], request: ChatRequest, llm_options: dict):
	"""SSE 流式响应，输出 OpenAI-compatible chunks。"""
	resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
	model_name = _CHAT_OPTIONS.agent_name(request)
	last_usage: dict[str, int] = {}
	has_tool_calls = False
	include_usage = _CHAT_OPTIONS.stream_include_usage(request)
	# 首个 chunk 发送 role
	yield _CHAT_PRESENTER.sse(_CHAT_PRESENTER.chunk(resp_id, model_name, [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]))
	async for event in agent.stream_with_messages(messages, session_id=request.session_id, llm_options=llm_options):
		if event.type == EventType.COST_UPDATE:
			last_usage = _usage(
				event.metadata.get("input_tokens", 0),
				event.metadata.get("output_tokens", 0),
			)
		elif event.type == EventType.STREAM_DELTA:
			yield _CHAT_PRESENTER.sse(_CHAT_PRESENTER.chunk(
				resp_id, model_name,
				[{"index": 0, "delta": {"content": event.content}, "finish_reason": None}],
			))
		elif event.type == EventType.TOOL_CALL:
			has_tool_calls = True
			yield _CHAT_PRESENTER.sse(_CHAT_PRESENTER.chunk(resp_id, model_name, [{
				"index": 0,
				"delta": {"tool_calls": [_CHAT_PRESENTER.tool_call_delta(
					index=0,
					call_id=event.tool_call_id,
					name=event.tool_name,
					arguments=json.dumps(event.arguments, ensure_ascii=False),
				)]},
				"finish_reason": None,
			}]))
		elif event.type == EventType.TOOL_ARGS_PREVIEW:
			has_tool_calls = True
			yield _CHAT_PRESENTER.sse(_CHAT_PRESENTER.chunk(resp_id, model_name, [{
				"index": 0,
				"delta": {"tool_calls": [_CHAT_PRESENTER.tool_call_delta(
					index=event.metadata.get("index", 0),
					call_id=event.tool_call_id,
					name=event.tool_name,
					arguments=event.content,
				)]},
				"finish_reason": None,
			}]))
		elif event.type == EventType.DONE:
			finish_reason = "tool_calls" if has_tool_calls else "stop"
			yield _CHAT_PRESENTER.sse(_CHAT_PRESENTER.chunk(resp_id, model_name, [{"index": 0, "delta": {}, "finish_reason": finish_reason}]))
			if include_usage:
				yield _CHAT_PRESENTER.sse(_CHAT_PRESENTER.chunk(resp_id, model_name, [], usage=last_usage or None))
		elif event.type == EventType.ERROR:
			chunk = _CHAT_PRESENTER.chunk(resp_id, model_name, [{"index": 0, "delta": {}, "finish_reason": "error"}])
			chunk["axc_event"] = {"type": "error", "data": {"content": event.content}}
			yield _CHAT_PRESENTER.sse(chunk)
	yield "data: [DONE]\n\n"
