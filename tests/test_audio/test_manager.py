"""Tests for AudioManager lifecycle."""
import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.audio.manager import AudioManager
from src.audio.mock import MockAudioCapturer, MockSTTEngine
from src.audio.recorder import AudioRecorder
from src.audio.trigger import SuggestionTrigger
from src.models.session import (
    InterviewSession, InterviewStage, SessionMetadata
)
from src.models.candidate import CandidateProfile


def _make_session() -> InterviewSession:
    return InterviewSession(
        id="mgr-test",
        candidate=CandidateProfile(id="c1", name="Test"),
        question_plan=[],
        rounds=[],
        stage=InterviewStage.INTERVIEWING,
        context_summary="",
        covered_dimensions=set(),
        metadata=SessionMetadata(candidate_id="c1", start_time=datetime.now()),
    )


@pytest.mark.asyncio
async def test_start_and_stop_lifecycle(tmp_path) -> None:
    capturer = MockAudioCapturer()
    candidate_stt = MockSTTEngine()
    interviewer_stt = MockSTTEngine()
    recorder = AudioRecorder()

    manager = AudioManager(capturer, candidate_stt, interviewer_stt, recorder)
    session = _make_session()
    ws_sender = AsyncMock()

    trigger = SuggestionTrigger(on_trigger=AsyncMock(), silence_threshold_sec=5.0)

    with patch.object(recorder, "start_recording", new=AsyncMock()) as mock_start, \
         patch.object(recorder, "stop_recording", new=AsyncMock(return_value=MagicMock(total_duration_sec=0.0))) as mock_stop:

        await manager.start(session, ws_sender, trigger)
        assert manager.transcription_manager is not None
        assert capturer.is_running

        result = await manager.stop()
        assert manager.transcription_manager is None
        mock_start.assert_called_once()
        mock_stop.assert_called_once()

    trigger.stop()


@pytest.mark.asyncio
async def test_transcription_manager_available_after_start(tmp_path) -> None:
    capturer = MockAudioCapturer()
    candidate_stt = MockSTTEngine()
    interviewer_stt = MockSTTEngine()
    recorder = AudioRecorder()

    manager = AudioManager(capturer, candidate_stt, interviewer_stt, recorder)
    session = _make_session()

    with patch.object(recorder, "start_recording", new=AsyncMock()), \
         patch.object(recorder, "stop_recording", new=AsyncMock(return_value=MagicMock(total_duration_sec=0.0))):

        trigger = SuggestionTrigger(on_trigger=AsyncMock(), silence_threshold_sec=5.0)
        await manager.start(session, AsyncMock(), trigger)

        assert manager.transcription_manager is not None

        await manager.stop()
        assert manager.transcription_manager is None

    trigger.stop()