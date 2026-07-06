"""Integration tests — GET /api/recovery/scan、POST /api/recovery/finish|discard"""

from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scan_returns_empty_when_no_orphans(client):
    r = await client.get("/api/recovery/scan")
    assert r.status_code == 200
    data = r.json()
    assert data["orphans"] == []
    assert data["count"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_finish_recovery_missing_params_returns_400(client):
    r = await client.post("/api/recovery/finish", json={})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_request"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discard_recovery_missing_params_returns_400(client):
    r = await client.post("/api/recovery/discard", json={})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "bad_request"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_finish_recovery_nonexistent_returns_404(client):
    r = await client.post(
        "/api/recovery/finish",
        json={"candidate_id": "not-exist", "interview_id": "not-exist"},
    )
    assert r.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discard_nonexistent_wal_returns_ok(client):
    """discard 一个不存在的 WAL 不应报错，deleted=False 即可。"""
    r = await client.post(
        "/api/recovery/discard",
        json={"candidate_id": "ghost-cid", "interview_id": "ghost-iid"},
    )
    assert r.status_code == 200
    assert "deleted" in r.json()
