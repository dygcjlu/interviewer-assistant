"""Integration tests — POST /api/candidate/select"""
from __future__ import annotations

import pytest

from src.models.candidate import CandidateProfile


async def _seed(client, cid: str, name: str) -> None:
    memory = client._transport.app.state.memory_module
    candidate = CandidateProfile(id=cid, name=name)
    await memory.save_candidate(candidate, f"# {name} 简历正文\n技术栈：Python, FastAPI")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_select_existing_candidate_returns_200(client):
    await _seed(client, "cid-sel-001", "选中候选人")
    r = await client.post(
        "/api/candidate/select", json={"candidate_id": "cid-sel-001"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["candidate_id"] == "cid-sel-001"
    assert data["profile"]["name"] == "选中候选人"
    assert "brief" in data
    assert "resume_markdown" in data
    assert "eval_report" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_select_candidate_updates_session(client):
    """select 后 /api/session/current 的 candidate_id 应更新。"""
    await _seed(client, "cid-sel-002", "更新会话候选人")
    await client.post(
        "/api/candidate/select", json={"candidate_id": "cid-sel-002"}
    )
    r = await client.get("/api/session/current")
    session = r.json()["session"]
    assert session is not None
    assert session["candidate_id"] == "cid-sel-002"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_select_nonexistent_candidate_returns_404(client):
    r = await client.post(
        "/api/candidate/select", json={"candidate_id": "not-exist"}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"
