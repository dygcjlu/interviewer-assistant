"""HTTP 请求日志中间件 — request_id、耗时、异常。"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.logging import bind_request_id, bind_session_id

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex[:8]
        bind_request_id(request_id)

        path = request.url.path
        method = request.method
        logger.info("HTTP start method=%s path=%s", method, path)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "HTTP failed method=%s path=%s elapsed_ms=%.1f",
                method,
                path,
                elapsed_ms,
            )
            raise

        elapsed_ms = (time.perf_counter() - start) * 1000
        orch = getattr(request.app.state, "orchestrator", None)
        if orch is not None:
            session = await orch.get_session()
            if session is not None:
                bind_session_id(session.id)

        logger.info(
            "HTTP done method=%s path=%s status_code=%d elapsed_ms=%.1f",
            method,
            path,
            response.status_code,
            elapsed_ms,
        )
        response.headers["X-Request-Id"] = request_id
        return response
