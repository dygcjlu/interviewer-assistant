"""Integration tests — GET /api/health"""

from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_returns_ok(client):
    """服务就绪时返回 200 和 status=ok。"""
    r = await client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["controller"] is True
    assert data["memory"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_not_ready_returns_503(tmp_path):
    """controller 或 memory 未注入时返回 503。"""
    from contextlib import asynccontextmanager

    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from src.web.app import create_app

    @asynccontextmanager
    async def empty_lifespan(app):
        # 不注入任何依赖，模拟初始化失败场景
        yield

    app = create_app(lifespan=empty_lifespan)
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/api/health")
            assert r.status_code == 503
            data = r.json()
            assert data["status"] == "not_ready"
