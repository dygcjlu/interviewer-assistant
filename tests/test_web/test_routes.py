"""Tests for web API routes."""
from __future__ import annotations

import io
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.agents.base import AgentResponse
from src.models.candidate import CandidateProfile
from src.models.session import (
    InterviewSession,
    InterviewStage,
    SessionMetadata,
)
from src.web.app import create_app


def _make_session() -> InterviewSession:
    candidate = CandidateProfile(id="c1", name="张三")
    return InterviewSession(
        id="s1",
        candidate=candidate,
        question_plan=[],
        rounds=[],
        stage=InterviewStage.RESUME_ANALYSIS,
        context_summary="",
        covered_dimensions=set(),
        working_notes="",
        metadata=SessionMetadata(candidate_id="c1", start_time=datetime.now()),
    )


def _make_app(orchestrator, memory_module=None):
    if memory_module is None:
        memory_module = MagicMock()
    settings = MagicMock()
    settings.RECORDINGS_DIR = "/tmp/recordings"
    return create_app(orchestrator, memory_module, None, settings)


# ── /api/session/current ──────────────────────────────────────────────────────

def test_session_current_returns_null_when_no_session() -> None:
    orch = MagicMock()
    orch.get_session = AsyncMock(return_value=None)
    app = _make_app(orch)
    with TestClient(app) as client:
        resp = client.get("/api/session/current")
    assert resp.status_code == 200
    assert resp.json()["session"] is None


def test_session_current_returns_session_info() -> None:
    session = _make_session()
    orch = MagicMock()
    orch.get_session = AsyncMock(return_value=session)
    orch.active_agent_name = "resume"
    orch.stage = InterviewStage.RESUME_ANALYSIS
    app = _make_app(orch)
    with TestClient(app) as client:
        resp = client.get("/api/session/current")
    assert resp.status_code == 200
    data = resp.json()["session"]
    assert data["id"] == "s1"
    assert data["candidate_name"] == "张三"
    assert data["active_agent"] == "resume"


# ── /api/interview/start ──────────────────────────────────────────────────────

def test_interview_start_creates_session_and_returns_stage() -> None:
    session = _make_session()
    session.stage = InterviewStage.INTERVIEWING

    orch = MagicMock()
    orch.get_session = AsyncMock(return_value=None)
    orch.create_session = AsyncMock(return_value=session)
    orch.switch_agent = AsyncMock()
    orch.handle_request = AsyncMock(return_value=AgentResponse(success=True))
    app = _make_app(orch)

    with TestClient(app) as client:
        resp = client.post("/api/interview/start", json={"candidate_id": "c1"})
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "s1"


def test_interview_start_returns_409_on_session_error() -> None:
    from src.models.exceptions import SessionError

    orch = MagicMock()
    orch.get_session = AsyncMock(return_value=_make_session())
    orch.switch_agent = AsyncMock(side_effect=SessionError("precondition failed"))
    app = _make_app(orch)

    with TestClient(app) as client:
        resp = client.post("/api/interview/start", json={"candidate_id": "c1"})
    assert resp.status_code == 409


# ── /api/interview/stop ───────────────────────────────────────────────────────

def test_interview_stop_switches_to_eval() -> None:
    session = _make_session()
    session.stage = InterviewStage.EVALUATING

    orch = MagicMock()
    orch.get_session = AsyncMock(return_value=session)
    orch.switch_agent = AsyncMock()
    app = _make_app(orch)

    with TestClient(app) as client:
        resp = client.post("/api/interview/stop")
    assert resp.status_code == 200
    orch.switch_agent.assert_awaited_once_with("eval")


def test_interview_stop_returns_409_with_no_session() -> None:
    orch = MagicMock()
    orch.get_session = AsyncMock(return_value=None)
    app = _make_app(orch)

    with TestClient(app) as client:
        resp = client.post("/api/interview/stop")
    assert resp.status_code == 409


# ── /api/session/switch ───────────────────────────────────────────────────────

def test_switch_agent_success() -> None:
    orch = MagicMock()
    orch.switch_agent = AsyncMock()
    orch.stage = InterviewStage.RESUME_ANALYSIS
    orch.active_agent_name = "resume"
    app = _make_app(orch)

    with TestClient(app) as client:
        resp = client.post("/api/session/switch", json={"target_agent": "resume"})
    assert resp.status_code == 200
    assert resp.json()["active_agent"] == "resume"


# ── /api/candidates ───────────────────────────────────────────────────────────

def test_list_candidates_empty() -> None:
    memory = MagicMock()
    memory.search_candidates = AsyncMock(return_value=[])
    orch = MagicMock()
    app = _make_app(orch, memory)

    with TestClient(app) as client:
        resp = client.get("/api/candidates")
    assert resp.status_code == 200
    assert resp.json()["candidates"] == []
    assert resp.json()["total"] == 0


def test_list_candidates_with_results() -> None:
    candidates = [CandidateProfile(id="c1", name="张三"), CandidateProfile(id="c2", name="李四")]
    memory = MagicMock()
    memory.search_candidates = AsyncMock(return_value=candidates)
    orch = MagicMock()
    app = _make_app(orch, memory)

    with TestClient(app) as client:
        resp = client.get("/api/candidates?keyword=张")
    assert resp.status_code == 200
    assert len(resp.json()["candidates"]) == 2


# ── /api/interview/suggest ────────────────────────────────────────────────────

def test_trigger_suggest_success() -> None:
    session = _make_session()
    orch = MagicMock()
    orch.get_session = AsyncMock(return_value=session)
    orch.handle_request = AsyncMock(
        return_value=AgentResponse(success=True, data={"request_id": 0, "status": "generating"})
    )
    app = _make_app(orch)

    with TestClient(app) as client:
        resp = client.post("/api/interview/suggest")
    assert resp.status_code == 200
    assert resp.json()["status"] == "generating"


def test_trigger_suggest_returns_409_no_session() -> None:
    orch = MagicMock()
    orch.get_session = AsyncMock(return_value=None)
    app = _make_app(orch)

    with TestClient(app) as client:
        resp = client.post("/api/interview/suggest")
    assert resp.status_code == 409