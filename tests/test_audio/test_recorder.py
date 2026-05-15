"""Tests for AudioRecorder."""
import asyncio
import os
import pytest
from pathlib import Path

from src.audio.recorder import AudioRecorder, RecordingResult, RoundSlice
from src.audio.protocol import AudioFrame


@pytest.mark.asyncio
async def test_start_creates_wav_files(tmp_path: Path) -> None:
    recorder = AudioRecorder()
    await recorder.start_recording("sess1", str(tmp_path))
    cand = tmp_path / "sess1" / "full_candidate.wav"
    intvw = tmp_path / "sess1" / "full_interviewer.wav"
    assert cand.exists()
    assert intvw.exists()
    await recorder.stop_recording()


@pytest.mark.asyncio
async def test_stop_returns_recording_result(tmp_path: Path) -> None:
    recorder = AudioRecorder()
    await recorder.start_recording("sess2", str(tmp_path))
    result = await recorder.stop_recording()
    assert isinstance(result, RecordingResult)
    assert result.session_id == "sess2"
    assert result.total_duration_sec >= 0.0


@pytest.mark.asyncio
async def test_mark_round_boundary_generates_slices(tmp_path: Path) -> None:
    recorder = AudioRecorder()
    await recorder.start_recording("sess3", str(tmp_path))
    recorder.mark_round_boundary(1)
    recorder.mark_round_boundary(2)
    result = await recorder.stop_recording()
    assert len(result.round_slices) == 2
    assert result.round_slices[0].round_number == 1
    assert result.round_slices[1].round_number == 2


@pytest.mark.asyncio
async def test_round_slice_files_created(tmp_path: Path) -> None:
    recorder = AudioRecorder()
    await recorder.start_recording("sess4", str(tmp_path))
    recorder.mark_round_boundary(1)
    result = await recorder.stop_recording()
    s = result.round_slices[0]
    assert Path(s.candidate_audio_path).exists()
    assert Path(s.interviewer_audio_path).exists()


@pytest.mark.asyncio
async def test_on_audio_frame_candidate(tmp_path: Path) -> None:
    recorder = AudioRecorder()
    await recorder.start_recording("sess5", str(tmp_path))
    frame = AudioFrame(data=b"\x00" * 320, source="candidate", timestamp=0.0)
    await recorder.on_audio_frame(frame)
    result = await recorder.stop_recording()
    cand_size = Path(result.full_candidate_path).stat().st_size
    # 44-byte WAV header + 320 bytes data
    assert cand_size == 44 + 320


@pytest.mark.asyncio
async def test_stop_without_boundaries_gives_empty_slices(tmp_path: Path) -> None:
    recorder = AudioRecorder()
    await recorder.start_recording("sess6", str(tmp_path))
    result = await recorder.stop_recording()
    assert result.round_slices == []