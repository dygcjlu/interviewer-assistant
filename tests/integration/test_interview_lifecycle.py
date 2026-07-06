"""Integration tests — 面试生命周期（start / stop / eval / brief）与状态机。"""

from __future__ import annotations

import json

import pytest

from src.llm.protocol import ChatResponse
from src.models.candidate import CandidateProfile
from src.models.session import InterviewStage


async def _seed(client, cid="cid-lc-001", name="生命周期候选人"):
    memory = client._transport.app.state.memory_module
    candidate = CandidateProfile(id=cid, name=name)
    await memory.save_candidate(candidate, f"# {name}\n技术栈：Python")
    return cid


# ── POST /api/interview/start ─────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_start_interview_returns_interviewing_stage(client):
    cid = await _seed(client)
    r = await client.post(
        "/api/interview/start", json={"candidate_id": cid, "trigger_mode": "manual"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["stage"] == "interviewing"
    assert "session_id" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_start_interview_without_prior_session_creates_session(client):
    cid = await _seed(client, "cid-lc-002", "无会话候选人")
    r = await client.post(
        "/api/interview/start", json={"candidate_id": cid, "trigger_mode": "auto"}
    )
    assert r.status_code == 200
    r2 = await client.get("/api/session/current")
    assert r2.json()["session"]["stage"] == "interviewing"


# ── POST /api/interview/stop ──────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stop_interview_returns_evaluating_stage(interviewing_client):
    r = await interviewing_client.post("/api/interview/stop")
    assert r.status_code == 200
    data = r.json()
    assert data["stage"] == "evaluating"
    assert "total_rounds" in data
    assert "session_id" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stop_interview_without_session_returns_409(client):
    r = await client.post("/api/interview/stop")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_session"


# ── 状态机转换 ────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_interview_state_machine_idle_to_interviewing(client):
    """状态机：idle → interviewing。"""
    cid = await _seed(client, "cid-sm-001", "状态机候选人")

    r_session = await client.get("/api/session/current")
    # 初始可能无 session 或 idle
    controller = client._transport.app.state.controller
    session = await controller.get_session()
    if session is None:
        session = await controller.create_session(cid)
    assert session.stage == InterviewStage.IDLE

    await client.post(
        "/api/interview/start", json={"candidate_id": cid, "trigger_mode": "manual"}
    )
    session = await controller.get_session()
    assert session.stage == InterviewStage.INTERVIEWING


@pytest.mark.integration
@pytest.mark.asyncio
async def test_interview_state_machine_interviewing_to_evaluating(interviewing_client):
    """状态机：interviewing → evaluating。"""
    controller = interviewing_client._transport.app.state.controller
    session = await controller.get_session()
    assert session.stage == InterviewStage.INTERVIEWING

    await interviewing_client.post("/api/interview/stop")
    session = await controller.get_session()
    assert session.stage == InterviewStage.EVALUATING


# ── GET /api/interview/brief ──────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_brief_returns_string(client):
    """brief 接口正常返回（内容可以为空字符串）。"""
    memory = client._transport.app.state.memory_module
    cid = "cid-brief-001"
    candidate = CandidateProfile(id=cid, name="简报候选人")
    await memory.save_candidate(candidate, "# 简历\n")
    await client.post(
        "/api/interview/start", json={"candidate_id": cid, "trigger_mode": "manual"}
    )

    r = await client.get("/api/interview/brief", params={"candidate_id": cid})
    assert r.status_code == 200
    assert "brief" in r.json()


# ── GET /api/interview/eval ───────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_eval_returns_report_structure(interviewing_client, mock_llm):
    """stop 后请求 eval，mock LLM 返回结构化报告，验证响应字段。

    interviewing_client 已注入 1 轮对话，stop → evaluating，eval → 200。
    """
    eval_json = json.dumps(
        {
            "dimensions": [
                {
                    "dimension": "系统设计",
                    "score": 8,
                    "comment": "表现良好",
                    "evidence": ["提到了分布式锁"],
                }
            ],
            "overall_score": 8.0,
            "strengths": ["系统思维清晰"],
            "weaknesses": ["缺乏 K8s 经验"],
            "recommendation": "hire",
            "summary": "整体优秀",
        },
        ensure_ascii=False,
    )
    # EvalAgent 调用一次 chat()（非流式）返回 JSON
    mock_llm.push_chat(
        ChatResponse(content=eval_json, prompt_tokens=100, completion_tokens=200)
    )

    await interviewing_client.post("/api/interview/stop")
    r = await interviewing_client.get("/api/interview/eval")

    assert r.status_code == 200
    data = r.json()
    assert "report" in data
    report = data["report"]
    assert "dimensions" in report
    assert "overall_score" in report
    assert "recommendation" in report
    assert report["recommendation"] in (
        "strong_hire",
        "hire",
        "weak_hire",
        "no_hire",
        "hire",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_eval_no_session_returns_409(client):
    r = await client.get("/api/interview/eval")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_session"
