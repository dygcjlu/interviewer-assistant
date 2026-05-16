"""FastAPI 应用工厂。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes import router
from .websocket import interview_ws_handler

logger = logging.getLogger(__name__)


def create_app(
    orchestrator: Any,
    memory_module: Any,
    context_manager: Any | None = None,
    settings: Any | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Interview Assistant starting up")
        yield
        logger.info("Interview Assistant shutting down")
        try:
            await orchestrator.close_session()
        except Exception:
            logger.exception("Lifespan: close_session failed")

    app = FastAPI(title="Interview Assistant API", version="1.0.0", lifespan=lifespan)

    app.state.orchestrator = orchestrator
    app.state.memory_module = memory_module
    app.state.context_manager = context_manager
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:8000", "http://localhost:8001"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    @app.websocket("/ws/interview")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await interview_ws_handler(websocket, orchestrator)

    frontend_dist = Path(__file__).parents[2] / "frontend" / "dist"
    if frontend_dist.exists():
        logger.info("Serving frontend from %s", frontend_dist)
        index_html = frontend_dist / "index.html"

        # Serve /assets/* (js/css bundles) before the SPA catch-all
        assets_dir = frontend_dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        # SPA catch-all: serve index.html for any non-API, non-WS, non-asset path
        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            return FileResponse(str(index_html))

    return app