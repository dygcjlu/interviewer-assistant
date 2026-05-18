"""Tests for TranscriptionManager."""
import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from src.audio.transcription import TranscriptionManager
from src.audio.trigger import SuggestionTrigger
from src.audio.recorder import AudioRecorder
from src.audio.protocol import TranscriptSegment
from src.models.session import (
    InterviewSession, InterviewStage, SessionMetadata, ConversationRound
)
from src.models.candidate import CandidateProfile


def _make_session() -> InterviewSession:
    return InterviewSession(
        id="test-session",
        candidate=CandidateProfile(id="c1", name="Test"),
        question_plan=[],
        rounds=[],
        stage=InterviewStage.INTERVIEWING,
        context_summary="",
        covered_dimensions=set(),
        working_notes="",
        metadata=SessionMetadata(candidate_id="c1", start_time=datetime.now()),
    )


def _make_segment(text: str, source: str, is_final: bool = True) -> TranscriptSegment:
    return TranscriptSegment(
        text=text,
        source=source,
        is_final=is_final,
        timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_on_segment_sends_ws_message() -> None:
    session = _make_session()
    ws_sender = AsyncMock()
    trigger = MagicMock(spec=SuggestionTrigger)
    recorder = MagicMock(spec=AudioRecorder)
    recorder.mark_round_boundary = MagicMock()

    mgr = TranscriptionManager(session, ws_sender, trigger, recorder)
    seg = _make_segment("hello", "candidate")
    await mgr.on_segment(seg)

    ws_sender.assert_called_once()
    call_args = ws_sender.call_args[0][0]
    assert call_args["type"] == "transcript"
    assert call_args["source"] == "candidate"
    assert call_args["text"] == "hello"


@pytest.mark.asyncio
async def test_candidate_text_accumulates() -> None:
    session = _make_session()
    ws_sender = AsyncMock()
    trigger = MagicMock(spec=SuggestionTrigger)
    recorder = MagicMock(spec=AudioRecorder)
    recorder.mark_round_boundary = MagicMock()

    mgr = TranscriptionManager(session, ws_sender, trigger, recorder)
    await mgr.on_segment(_make_segment("hello", "candidate"))
    await mgr.on_segment(_make_segment("world", "candidate"))

    _, cand = mgr.get_current_round_text()
    assert "hello" in cand
    assert "world" in cand


@pytest.mark.asyncio
async def test_finalize_round_creates_conversation_round() -> None:
    session = _make_session()
    ws_sender = AsyncMock()
    trigger = MagicMock(spec=SuggestionTrigger)
    recorder = MagicMock(spec=AudioRecorder)
    recorder.mark_round_boundary = MagicMock()

    mgr = TranscriptionManager(session, ws_sender, trigger, recorder)
    await mgr.on_segment(_make_segment("tell me about yourself", "interviewer"))
    await mgr.on_segment(_make_segment("I am a developer", "candidate"))

    # Force finalize
    round_ = await mgr.finalize_round()
    assert isinstance(round_, ConversationRound)
    assert round_.round_number == 1
    assert len(session.rounds) == 1


@pytest.mark.asyncio
async def test_round_boundary_fires_when_interviewer_speaks_after_candidate() -> None:
    session = _make_session()
    ws_sender = AsyncMock()
    trigger = MagicMock(spec=SuggestionTrigger)
    recorder = MagicMock(spec=AudioRecorder)
    recorder.mark_round_boundary = MagicMock()

    mgr = TranscriptionManager(session, ws_sender, trigger, recorder)
    # Interviewer asks
    await mgr.on_segment(_make_segment("Question 1?", "interviewer"))
    # Candidate answers
    await mgr.on_segment(_make_segment("Answer 1", "candidate"))
    # Interviewer speaks again → triggers finalize
    await mgr.on_segment(_make_segment("Question 2?", "interviewer"))

    assert len(session.rounds) == 1
    recorder.mark_round_boundary.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_non_final_segment_not_accumulated() -> None:
    session = _make_session()
    ws_sender = AsyncMock()
    trigger = MagicMock(spec=SuggestionTrigger)
    recorder = MagicMock(spec=AudioRecorder)
    recorder.mark_round_boundary = MagicMock()

    mgr = TranscriptionManager(session, ws_sender, trigger, recorder)
    await mgr.on_segment(_make_segment("partial", "candidate", is_final=False))

    _, cand = mgr.get_current_round_text()
    assert cand == ""


@pytest.mark.asyncio
async def test_flush_pending_round_noop_when_empty() -> None:
    session = _make_session()
    ws_sender = AsyncMock()
    trigger = MagicMock(spec=SuggestionTrigger)
    recorder = MagicMock(spec=AudioRecorder)
    recorder.mark_round_boundary = MagicMock()

    mgr = TranscriptionManager(session, ws_sender, trigger, recorder)
    result = await mgr.flush_pending_round()
    assert result is None
    assert len(session.rounds) == 0


@pytest.mark.asyncio
async def test_flush_pending_round_finalizes_candidate_text() -> None:
    session = _make_session()
    ws_sender = AsyncMock()
    trigger = MagicMock(spec=SuggestionTrigger)
    recorder = MagicMock(spec=AudioRecorder)
    recorder.mark_round_boundary = MagicMock()

    mgr = TranscriptionManager(session, ws_sender, trigger, recorder)
    await mgr.on_segment(_make_segment("my answer", "candidate"))
    result = await mgr.flush_pending_round()

    assert result is not None
    assert len(session.rounds) == 1
    assert session.rounds[0].candidate_text == "my answer"


@pytest.mark.asyncio
async def test_finalize_round_broadcasts_session_snapshot() -> None:
    session = _make_session()
    ws_sender = AsyncMock()
    trigger = MagicMock(spec=SuggestionTrigger)
    recorder = MagicMock(spec=AudioRecorder)
    recorder.mark_round_boundary = MagicMock()

    mgr = TranscriptionManager(session, ws_sender, trigger, recorder)
    await mgr.on_segment(_make_segment("answer", "candidate"))
    await mgr.finalize_round()

    snapshot_calls = [
        c[0][0]
        for c in ws_sender.call_args_list
        if c[0][0].get("type") == "session_snapshot"
    ]
    assert snapshot_calls
    assert snapshot_calls[-1]["rounds_count"] == 1


@pytest.mark.asyncio
async def test_suggestion_trigger_called_on_candidate_final() -> None:
    session = _make_session()
    ws_sender = AsyncMock()
    trigger = MagicMock(spec=SuggestionTrigger)
    trigger.on_candidate_segment = MagicMock()
    recorder = MagicMock(spec=AudioRecorder)
    recorder.mark_round_boundary = MagicMock()

    mgr = TranscriptionManager(session, ws_sender, trigger, recorder)
    seg = _make_segment("I used Redis", "candidate", is_final=True)
    await mgr.on_segment(seg)

    trigger.on_candidate_segment.assert_called_once_with(seg)