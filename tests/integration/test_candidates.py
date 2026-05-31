"""Integration tests — GET /api/candidates 和 DELETE /api/candidates/{id}"""
from __future__ import annotations

import pytest

from src.models.candidate import CandidateProfile


async def _seed_candidate(client, cid: str, name: str) -> CandidateProfile:
    memory = client._transport.app.state.memory_module
    candidate = CandidateProfile(id=cid, name=name)
    await memory.save_candidate(candidate, f"# {name} 简历\n")
    return candidate


# ── list candidates ───────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_candidates_empty(client):
    r = await client.get("/api/candidates")
    assert r.status_code == 200
    data = r.json()
    assert data["candidates"] == []
    assert data["total"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_candidates_returns_saved_entries(client):
    await _seed_candidate(client, "cid-001", "候选人甲")
    await _seed_candidate(client, "cid-002", "候选人乙")

    r = await client.get("/api/candidates")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 2
    names = [c["name"] for c in data["candidates"]]
    assert "候选人甲" in names
    assert "候选人乙" in names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_candidates_keyword_filters(client):
    await _seed_candidate(client, "cid-py-001", "Python 工程师")
    await _seed_candidate(client, "cid-go-001", "Go 工程师")

    r = await client.get("/api/candidates", params={"keyword": "Python"})
    assert r.status_code == 200
    names = [c["name"] for c in r.json()["candidates"]]
    assert "Python 工程师" in names
    # Go 工程师不应出现（关键词不匹配）
    assert "Go 工程师" not in names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_candidates_pagination(client):
    for i in range(5):
        await _seed_candidate(client, f"cid-page-{i}", f"候选人{i}")

    r = await client.get("/api/candidates", params={"limit": 2, "offset": 0})
    assert len(r.json()["candidates"]) <= 2


# ── delete candidate ──────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_candidate_returns_200(client):
    await _seed_candidate(client, "cid-del-001", "待删除候选人")
    r = await client.delete("/api/candidates/cid-del-001")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_candidate_removes_from_list(client):
    await _seed_candidate(client, "cid-del-002", "会消失的候选人")
    await client.delete("/api/candidates/cid-del-002")

    r = await client.get("/api/candidates")
    names = [c["name"] for c in r.json()["candidates"]]
    assert "会消失的候选人" not in names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_nonexistent_candidate_returns_404(client):
    r = await client.delete("/api/candidates/nonexistent-id")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_candidate_in_use_returns_409(interviewing_client):
    """面试进行中的候选人不允许删除。"""
    controller = interviewing_client._transport.app.state.controller
    session = await controller.get_session()
    cid = session.candidate.id

    r = await interviewing_client.delete(f"/api/candidates/{cid}")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "candidate_in_use"
