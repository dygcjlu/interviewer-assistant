"""Unit tests — TranscriptionManager：on_segment、finalize_round、flush_pending_round。"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.audio.protocol import TranscriptSegment
from src.audio.transcription import TranscriptionManager
from src.audio.trigger import SuggestionTrigger
from src.models.candidate import CandidateProfile
from src.models.session import ConversationRound, InterviewSession, InterviewStage, SessionMetadata


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_session() -> InterviewSession:
    return InterviewSession(
        id="s-001",
        candidate=CandidateProfile(id="c-001", name="张三"),
        rounds=[],
        stage=InterviewStage.INTERVIEWING,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id="c-001", start_time=datetime.now()),
    )


def _make_trigger() -> SuggestionTrigger:
    return SuggestionTrigger(
        on_trigger=AsyncMock(),
        silence_threshold_sec=0.01,
        min_interval_sec=0.0,
    )


def _make_manager(session=None) -> tuple[TranscriptionManager, list, MagicMock]:
    if session is None:
        session = _make_session()
    ws_messages = []

    async def ws_sender(msg):
        ws_messages.append(msg)

    trigger = _make_trigger()
    recorder = MagicMock()
    recorder.mark_round_boundary = MagicMock()
    manager = TranscriptionManager(
        session=session,
        ws_sender=ws_sender,
        suggestion_trigger=trigger,
        recorder=recorder,
    )
    return manager, ws_messages, recorder


def _segment(text: str, is_final: bool, source: str = "candidate") -> TranscriptSegment:
    return TranscriptSegment(text=text, is_final=is_final, source=source, timestamp=1.0)


# ── TranscriptionManager ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestTranscriptionManager:
    @pytest.mark.asyncio
    async def test_on_segment_pushes_to_ws(self):
        mgr, msgs, _ = _make_manager()
        await mgr.on_segment(_segment("中间词", False))
        assert len(msgs) >= 1
        assert msgs[0]["type"] == "transcript"

    @pytest.mark.asyncio
    async def test_on_segment_not_final_does_not_finalize(self):
        session = _make_session()
        mgr, _, _ = _make_manager(session)
        await mgr.on_segment(_segment("部分文本", False, "candidate"))
        assert len(session.rounds) == 0

    @pytest.mark.asyncio
    async def test_on_segment_candidate_final_accumulates_text(self):
        mgr, _, _ = _make_manager()
        await mgr.on_segment(_segment("你好", True, "candidate"))
        ivr, cand = mgr.get_current_round_text()
        assert "你好" in cand

    @pytest.mark.asyncio
    async def test_on_segment_interviewer_after_candidate_finalizes_round(self):
        session = _make_session()
        mgr, _, _ = _make_manager(session)
        await mgr.on_segment(_segment("回答", True, "candidate"))
        await mgr.on_segment(_segment("新问题", True, "interviewer"))
        assert len(session.rounds) == 1

    @pytest.mark.asyncio
    async def test_finalize_round_creates_round(self):
        session = _make_session()
        mgr, _, _ = _make_manager(session)
        await mgr.on_segment(_segment("问题", True, "interviewer"))
        await mgr.on_segment(_segment("回答", True, "candidate"))
        round_ = await mgr.finalize_round()
        assert round_.round_number == 1
        assert len(session.rounds) == 1

    @pytest.mark.asyncio
    async def test_finalize_round_increments_counter(self):
        mgr, _, _ = _make_manager()
        await mgr.finalize_round()
        await mgr.finalize_round()
        # round_number 应为 3（第三轮开始）
        assert mgr._round_number == 3

    @pytest.mark.asyncio
    async def test_finalize_round_sends_session_snapshot(self):
        mgr, msgs, _ = _make_manager()
        await mgr.finalize_round()
        snapshot_msgs = [m for m in msgs if m.get("type") == "session_snapshot"]
        assert len(snapshot_msgs) >= 1

    @pytest.mark.asyncio
    async def test_finalize_round_calls_recorder_boundary(self):
        mgr, _, recorder = _make_manager()
        await mgr.finalize_round()
        recorder.mark_round_boundary.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_flush_pending_round_when_no_pending(self):
        mgr, _, _ = _make_manager()
        result = await mgr.flush_pending_round()
        assert result is None

    @pytest.mark.asyncio
    async def test_flush_pending_round_when_has_pending(self):
        session = _make_session()
        mgr, _, _ = _make_manager(session)
        await mgr.on_segment(_segment("问题", True, "interviewer"))
        result = await mgr.flush_pending_round()
        assert result is not None
        assert len(session.rounds) == 1

    @pytest.mark.asyncio
    async def test_has_pending_round_false_initially(self):
        mgr, _, _ = _make_manager()
        assert not mgr.has_pending_round()

    @pytest.mark.asyncio
    async def test_has_pending_round_true_after_segment(self):
        mgr, _, _ = _make_manager()
        await mgr.on_segment(_segment("问题", True, "interviewer"))
        assert mgr.has_pending_round()

    @pytest.mark.asyncio
    async def test_on_round_finalized_callback_invoked(self):
        finalized = []

        async def on_finalized(round_: ConversationRound) -> None:
            finalized.append(round_)

        session = _make_session()
        trigger = _make_trigger()
        recorder = MagicMock()
        recorder.mark_round_boundary = MagicMock()
        manager = TranscriptionManager(
            session=session,
            ws_sender=AsyncMock(),
            suggestion_trigger=trigger,
            recorder=recorder,
            on_round_finalized=on_finalized,
        )
        await manager.finalize_round()
        assert len(finalized) == 1

    @pytest.mark.asyncio
    async def test_multiple_candidate_segments_accumulate(self):
        mgr, _, _ = _make_manager()
        await mgr.on_segment(_segment("第一句", True, "candidate"))
        await mgr.on_segment(_segment("第二句", True, "candidate"))
        _, cand = mgr.get_current_round_text()
        assert "第一句" in cand
        assert "第二句" in cand
