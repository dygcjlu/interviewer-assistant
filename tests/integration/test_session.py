"""Integration tests — GET /api/session/current"""
from __future__ import annotations

import pytest

from src.models.candidate import CandidateProfile


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_current_no_session_returns_null(client):
    """无活跃会话时返回 {"session": null}。"""
    r = await client.get("/api/session/current")
    assert r.status_code == 200
    assert r.json()["session"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_current_with_active_session_returns_fields(client):
    """有活跃会话时返回完整会话信息。"""
    memory = client._transport.app.state.memory_module
    candidate = CandidateProfile(id="cid-sess-001", name="会话候选人")
    await memory.save_candidate(candidate, "# 简历\n")

    await client.post(
        "/api/interview/start",
        json={"candidate_id": "cid-sess-001", "trigger_mode": "manual"},
    )

    r = await client.get("/api/session/current")
    assert r.status_code == 200
    session = r.json()["session"]
    assert session is not None
    assert session["stage"] == "interviewing"
    assert session["candidate_id"] == "cid-sess-001"
    assert session["candidate_name"] == "会话候选人"
    assert "rounds_count" in session
    assert "token_used" in session
    assert "token_budget" in session


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_current_rounds_count_increments(client):
    """rounds_count 反映已归档的对话轮次数量。"""
    memory = client._transport.app.state.memory_module
    cid = "cid-sess-002"
    candidate = CandidateProfile(id=cid, name="轮次候选人")
    await memory.save_candidate(candidate, "# 简历\n")
    await client.post(
        "/api/interview/start", json={"candidate_id": cid, "trigger_mode": "manual"}
    )

    r = await client.get("/api/session/current")
    assert r.json()["session"]["rounds_count"] == 0
