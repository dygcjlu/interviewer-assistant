"""Unit tests — InterviewController: 会话生命周期、状态机转换。"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.interview_controller import InterviewController, _broadcast, _noop_ws_sender
from src.models.candidate import CandidateProfile
from src.models.exceptions import SessionError
from src.models.session import InterviewSession, InterviewStage, SessionMetadata


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_memory(tmp_path: Path):
    from src.storage.memory_module import MemoryModule
    candidates_dir = tmp_path / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    return MemoryModule(candidates_dir=str(candidates_dir))


def _make_audio_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.start = AsyncMock()
    mgr.stop = AsyncMock()
    return mgr


def _make_interview_agent() -> MagicMock:
    from src.framework.context import ContextConfig, ContextManager
    mock_llm = MagicMock()
    mock_llm.count_tokens = MagicMock(return_value=100)
    ctx_config = ContextConfig(window_size=3)
    ctx_manager = ContextManager(ctx_config, mock_llm)

    agent = MagicMock()
    agent.on_activate = AsyncMock()
    agent.on_deactivate = AsyncMock()
    agent.context_manager = ctx_manager
    agent.suggestion_trigger = None
    agent.attach_ws_sender = MagicMock()
    agent.set_current_round_getter = MagicMock()
    return agent


def _make_eval_agent() -> MagicMock:
    agent = MagicMock()
    agent.handle_request = AsyncMock(return_value=MagicMock(success=True, data={"eval": "done"}))
    return agent


def _make_controller(tmp_path: Path) -> InterviewController:
    memory = _make_memory(tmp_path)
    audio = _make_audio_manager()
    interview_agent = _make_interview_agent()
    eval_agent = _make_eval_agent()
    return InterviewController(interview_agent, eval_agent, memory, audio)


# ── _broadcast helper ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_calls_all_senders(self):
        received = []
        async def s1(msg): received.append(("s1", msg))
        async def s2(msg): received.append(("s2", msg))
        senders = {1: s1, 2: s2}
        await _broadcast(senders, {"type": "test"})
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_senders(self):
        async def failing(msg): raise RuntimeError("dead")
        senders = {1: failing}
        await _broadcast(senders, {"type": "test"})
        assert 1 not in senders

    @pytest.mark.asyncio
    async def test_noop_ws_sender(self):
        result = await _noop_ws_sender({"type": "ping"})
        assert result is None


# ── create_session ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCreateSession:
    @pytest.mark.asyncio
    async def test_create_session_no_candidate_id(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        session = await ctrl.create_session()
        assert session is not None
        assert session.candidate.id != ""
        assert session.stage == InterviewStage.IDLE

    @pytest.mark.asyncio
    async def test_create_session_with_candidate_id(self, tmp_path):
        from src.storage.memory_module import MemoryModule
        memory = _make_memory(tmp_path)
        profile = CandidateProfile(id="c-001", name="张三", skills=["Python"])
        await memory.save_candidate(profile, "")
        ctrl = InterviewController(_make_interview_agent(), _make_eval_agent(), memory, _make_audio_manager())
        session = await ctrl.create_session(candidate_id="c-001")
        assert session.candidate.name == "张三"

    @pytest.mark.asyncio
    async def test_create_session_with_nonexistent_candidate_id(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        session = await ctrl.create_session(candidate_id="nonexistent")
        assert session.candidate.id == "nonexistent"
        assert session.candidate.name == ""

    @pytest.mark.asyncio
    async def test_get_session_returns_created(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        created = await ctrl.create_session()
        retrieved = await ctrl.get_session()
        assert retrieved is created

    @pytest.mark.asyncio
    async def test_get_session_returns_none_initially(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        result = await ctrl.get_session()
        assert result is None


# ── close_session ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCloseSession:
    @pytest.mark.asyncio
    async def test_close_session_when_none_does_not_raise(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        await ctrl.close_session()
        assert await ctrl.get_session() is None

    @pytest.mark.asyncio
    async def test_close_session_clears_session(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        profile = CandidateProfile(id="c-001", name="张三")
        await ctrl._memory.save_candidate(profile, "")
        await ctrl.create_session(candidate_id="c-001")
        await ctrl.close_session()
        assert await ctrl.get_session() is None

    @pytest.mark.asyncio
    async def test_close_session_when_interviewing_stops_audio(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        profile = CandidateProfile(id="c-001", name="张三")
        await ctrl._memory.save_candidate(profile, "")
        session = await ctrl.create_session(candidate_id="c-001")
        session.stage = InterviewStage.INTERVIEWING
        ctrl._memory.start_interview = AsyncMock()
        await ctrl.close_session()
        ctrl._audio.stop.assert_called_once()


# ── start_interview ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestStartInterview:
    @pytest.mark.asyncio
    async def test_start_interview_raises_when_no_session(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        with pytest.raises(SessionError, match="当前没有活跃会话"):
            await ctrl.start_interview()

    @pytest.mark.asyncio
    async def test_start_interview_raises_when_already_interviewing(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        profile = CandidateProfile(id="c-001", name="张三")
        await ctrl._memory.save_candidate(profile, "")
        session = await ctrl.create_session(candidate_id="c-001")
        session.stage = InterviewStage.INTERVIEWING
        ctrl._memory.start_interview = AsyncMock()
        with pytest.raises(SessionError, match="面试已在进行中"):
            await ctrl.start_interview()

    @pytest.mark.asyncio
    async def test_start_interview_raises_when_no_candidate(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        session = await ctrl.create_session()
        session.candidate.id = ""
        ctrl._memory.start_interview = AsyncMock()
        with pytest.raises(SessionError, match="候选人"):
            await ctrl.start_interview()

    @pytest.mark.asyncio
    async def test_start_interview_activates_agent(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        profile = CandidateProfile(id="c-001", name="张三")
        await ctrl._memory.save_candidate(profile, "")
        await ctrl.create_session(candidate_id="c-001")
        ctrl._audio.start = AsyncMock()
        ctrl._memory.start_interview = AsyncMock()
        await ctrl.start_interview()
        ctrl._interview_agent.on_activate.assert_called_once()


# ── ws_senders management ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestWsSenders:
    @pytest.mark.asyncio
    async def test_add_and_remove_ws_sender(self, tmp_path):
        ctrl = _make_controller(tmp_path)
        received = []
        async def sender(msg): received.append(msg)
        ctrl._ws_senders[1] = sender
        assert 1 in ctrl._ws_senders
        del ctrl._ws_senders[1]
        assert 1 not in ctrl._ws_senders
