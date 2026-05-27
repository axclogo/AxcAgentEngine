"""FastAPI 应用工厂。"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from fastapi import FastAPI
	from axc_agent_engine.engine import Engine

logger = logging.getLogger(__name__)


def create_app(engine: Engine, agents_dir: str = "") -> FastAPI:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 FastAPI 应用"""
	from fastapi import FastAPI
	from fastapi.responses import JSONResponse
	from starlette.requests import Request
	from axc_agent_engine.api.routes.chat import create_chat_router
	from axc_agent_engine.api.routes.health import router as health_router
	from axc_agent_engine.api.deps import EngineState
	from axc_agent_engine.core.errors import (
		AxcError, ProviderError, TimeoutError, ConfigError,
		CancelledError, SchemaError, ToolError,
	)

	app = FastAPI(title="AxcAgentEngine", version="2.1.0")
	state = EngineState(engine=engine, agents_dir=agents_dir)
	app.state.engine_state = state

	_ERROR_STATUS_MAP: dict[type, int] = {
		ProviderError: 502,
		TimeoutError: 504,
		ConfigError: 400,
		CancelledError: 499,
		SchemaError: 400,
		ToolError: 500,
	}

	@app.exception_handler(AxcError)
	async def axc_error_handler(request: Request, exc: AxcError) -> JSONResponse:
		status_code = 500
		for err_cls, code in _ERROR_STATUS_MAP.items():
			if isinstance(exc, err_cls):
				status_code = code
				break
		return JSONResponse(status_code=status_code, content={"error": type(exc).__name__, "detail": str(exc)})

	app.include_router(health_router)
	app.include_router(create_chat_router(state))

	logger.info(f"API started, agents_dir={agents_dir}")
	return app
