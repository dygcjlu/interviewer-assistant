"""FastAPI 应用工厂。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from .middleware import RequestLoggingMiddleware
from .routes import router
from .websocket import interview_ws_handler

logger = logging.getLogger(__name__)


def create_app(lifespan: Any = None) -> FastAPI:
    app = FastAPI(
        title="Interview Assistant API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    app.include_router(router)

    @app.websocket("/ws/interview")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await interview_ws_handler(websocket, app.state.controller)

    return app
