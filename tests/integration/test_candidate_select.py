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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_select_candidate_injects_history_into_main_agent(client):
    """选中有历史记录的候选人后，MainAgent 的系统提示应包含历史面试摘要。"""
    from unittest.mock import AsyncMock, patch
    from src.storage.memory_module import CandidateHistory, InterviewSummary
    from datetime import datetime

    await _seed(client, "cid-hist-001", "历史候选人")

    fake_history = CandidateHistory(
        past_interviews=[
            InterviewSummary(
                interview_id="s-old",
                date=datetime(2025, 1, 1, 10, 0),
                overall_score=6.0,
                recommendation="weak_hire",
                key_findings="表现一般",
            )
        ],
        history_summary="候选人 历史候选人 历史面试记录：\n第1次面试：2025-01-01，评分 6.0，结论 weak_hire",
    )

    memory = client._transport.app.state.memory_module
    main_agent = client._transport.app.state.main_agent

    with patch.object(memory, "get_candidate_history", new=AsyncMock(return_value=fake_history)):
        r = await client.post(
            "/api/candidate/select", json={"candidate_id": "cid-hist-001"}
        )

    assert r.status_code == 200
    prompt = main_agent._build_system_prompt()
    assert "历史面试记录" in prompt
    assert "weak_hire" in prompt


@pytest.mark.integration
@pytest.mark.asyncio
async def test_select_candidate_no_history_does_not_break(client):
    """候选人无历史记录时，select 路由正常完成，不注入历史字段。"""
    from unittest.mock import AsyncMock, patch

    await _seed(client, "cid-hist-002", "无历史候选人")

    memory = client._transport.app.state.memory_module

    main_agent = client._transport.app.state.main_agent

    with patch.object(memory, "get_candidate_history", new=AsyncMock(return_value=None)):
        r = await client.post(
            "/api/candidate/select", json={"candidate_id": "cid-hist-002"}
        )

    assert r.status_code == 200
    prompt = main_agent._build_system_prompt()
    assert "历史面试记录" not in prompt


@pytest.mark.integration
@pytest.mark.asyncio
async def test_select_candidate_history_exception_does_not_break(client):
    """get_candidate_history 抛出异常时，select 路由仍返回 200，不注入历史。"""
    from unittest.mock import AsyncMock, patch

    await _seed(client, "cid-hist-003", "异常候选人")

    memory = client._transport.app.state.memory_module
    main_agent = client._transport.app.state.main_agent

    with patch.object(memory, "get_candidate_history", new=AsyncMock(side_effect=OSError("disk error"))):
        r = await client.post(
            "/api/candidate/select", json={"candidate_id": "cid-hist-003"}
        )

    assert r.status_code == 200
    prompt = main_agent._build_system_prompt()
    assert "历史面试记录" not in prompt
