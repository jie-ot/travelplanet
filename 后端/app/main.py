"""FastAPI application entry point.

Wires up CORS (from config), static mounting, global exception handlers that
enforce the standard response envelope, and the aggregate API router. Importing
this module must not start a server; `uvicorn.run` only fires under
`__main__` (see 《后端技术栈与全局规范》一.3、四、七).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from app.api.api import api_router
from app.core.config import settings
from app.core.exceptions import BusinessError
from app.core.responses import (
    CODE_INTERNAL_ERROR,
    CODE_INVALID_PARAM,
    error,
)

logger = logging.getLogger("travelplanet")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run lightweight startup baseline checks (demo memory + default cover)."""
    from app.db.session import engine
    from app.services.bootstrap_service import ensure_demo_baseline

    with Session(engine) as session:
        ensure_demo_baseline(session)

    # Warm A′ community MCP sessions before accepting traffic when configured.
    # An unavailable optional MCP is logged and degraded; it never aborts startup.
    await _prewarm_mcp()
    yield


async def _prewarm_mcp() -> None:
    """Warm enabled A′ MCP endpoints and optionally await terminal status."""
    try:
        from app.ai.tools import mcp_stdio

        endpoints: list[tuple[str, str]] = []
        if settings.TOOLS_ENABLED and settings.RAIL_MCP_ENABLED and settings.RAIL_MCP_ENDPOINT:
            mcp_stdio.prewarm(settings.RAIL_MCP_ENDPOINT)
            endpoints.append(("rail", settings.RAIL_MCP_ENDPOINT))
        if not settings.MCP_WAIT_READY_ON_STARTUP or not endpoints:
            return

        timeout = float(settings.MCP_STARTUP_TIMEOUT_SECONDS) + 1.0
        statuses = await asyncio.gather(
            *(
                asyncio.to_thread(mcp_stdio.wait_until_ready, endpoint, timeout)
                for _, endpoint in endpoints
            )
        )
        for (name, endpoint), status in zip(endpoints, statuses, strict=True):
            if status == "ready":
                logger.info("MCP startup ready (name=%s, endpoint=%s)", name, endpoint)
            else:
                logger.warning(
                    "MCP startup unavailable (name=%s, endpoint=%s, status=%s)",
                    name,
                    endpoint,
                    status,
                )
    except Exception:  # noqa: BLE001
        logger.exception("MCP prewarm skipped due to error")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(title="旅行星球 Backend", version="0.1.0", lifespan=lifespan)

    # —— CORS：严格从配置解析 ——
    allow_origins = settings.frontend_origins_list or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # —— 静态资源挂载 ——
    static_root = os.path.abspath(settings.STATIC_ROOT)
    os.makedirs(static_root, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_root), name="static")

    # —— 全局异常处理器：统一标准响应体 ——
    _register_exception_handlers(app)

    # —— 业务路由 ——
    app.include_router(api_router, prefix="/api")

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Register handlers that translate exceptions into the standard envelope."""

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic / FastAPI request validation → HTTP 200 + code 1002.
        detail = _summarize_validation_error(exc)
        return JSONResponse(
            status_code=200,
            content=error(CODE_INVALID_PARAM, f"参数校验失败：{detail}"),
        )

    @app.exception_handler(BusinessError)
    async def _business_handler(request: Request, exc: BusinessError) -> JSONResponse:
        return JSONResponse(status_code=200, content=error(exc.code, exc.message))

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never let a framework 500 leak to the frontend; do not expose details.
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=200,
            content=error(CODE_INTERNAL_ERROR, "服务异常，请稍后再试"),
        )


def _summarize_validation_error(exc: RequestValidationError) -> str:
    """Build a short, human-readable summary of validation errors."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err.get("loc", ()) if item != "body")
        msg = err.get("msg", "invalid")
        parts.append(f"{loc or 'body'}: {msg}")
    return "; ".join(parts) if parts else "请求参数不合法"


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
