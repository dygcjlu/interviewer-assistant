"""录音管理器 — 完整录音 + 按轮次切片。"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass
from pathlib import Path

import aiofiles

from .protocol import AudioFrame

logger = logging.getLogger(__name__)

# WAV audio parameters
_SAMPLE_RATE = 16000
_CHANNELS = 1
_BITS = 16


def _wav_header(data_size: int = 0) -> bytes:
    byte_rate = _SAMPLE_RATE * _CHANNELS * _BITS // 8
    block_align = _CHANNELS * _BITS // 8
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        _CHANNELS,
        _SAMPLE_RATE,
        byte_rate,
        block_align,
        _BITS,
        b"data",
        data_size,
    )


@dataclass
class RoundSlice:
    round_number: int
    candidate_audio_path: str
    interviewer_audio_path: str
    start_time_sec: float
    end_time_sec: float


@dataclass
class RecordingResult:
    session_id: str
    full_candidate_path: str
    full_interviewer_path: str
    round_slices: list[RoundSlice]
    total_duration_sec: float


class AudioRecorder:
    """录音管理器 — 完整录音 + 按轮次切片。"""

    def __init__(self) -> None:
        self._session_id: str = ""
        self._recordings_dir: str = "recordings"
        self._session_dir: Path | None = None
        self._candidate_path: str = ""
        self._interviewer_path: str = ""
        self._candidate_file: aiofiles.threadpool.binary.AsyncBufferedIOBase | None = (
            None
        )
        self._interviewer_file: (
            aiofiles.threadpool.binary.AsyncBufferedIOBase | None
        ) = None
        self._start_time: float = 0.0
        self._candidate_bytes: int = 0
        self._interviewer_bytes: int = 0
        # (round_number, timestamp)
        self._round_boundaries: list[tuple[int, float]] = []
        self._is_recording: bool = False

    async def start_recording(
        self, session_id: str, recordings_dir: str = "recordings"
    ) -> None:
        """创建 recordings/{session_id}/ 目录，初始化两个 WAV 文件。"""
        self._session_id = session_id
        self._recordings_dir = recordings_dir
        self._session_dir = Path(recordings_dir) / session_id
        rounds_dir = self._session_dir / "rounds"
        rounds_dir.mkdir(parents=True, exist_ok=True)

        self._candidate_path = str(self._session_dir / "full_candidate.wav")
        self._interviewer_path = str(self._session_dir / "full_interviewer.wav")

        self._candidate_file = await aiofiles.open(self._candidate_path, "wb")
        await self._candidate_file.write(_wav_header(0))

        self._interviewer_file = await aiofiles.open(self._interviewer_path, "wb")
        await self._interviewer_file.write(_wav_header(0))

        self._start_time = time.monotonic()
        self._candidate_bytes = 0
        self._interviewer_bytes = 0
        self._round_boundaries = []
        self._is_recording = True
        logger.info("AudioRecorder: started recording session=%s", session_id)

    async def on_audio_frame(self, frame: AudioFrame) -> None:
        """接收音频帧，分声道写入 WAV。"""
        if not self._is_recording:
            return
        if frame.source in ("candidate", "mixed") and self._candidate_file:
            await self._candidate_file.write(frame.data)
            self._candidate_bytes += len(frame.data)
        if frame.source in ("interviewer", "mixed") and self._interviewer_file:
            await self._interviewer_file.write(frame.data)
            self._interviewer_bytes += len(frame.data)

    def mark_round_boundary(self, round_number: int) -> None:
        """记录当前时间戳作为轮次边界。"""
        ts = time.monotonic() - self._start_time
        self._round_boundaries.append((round_number, ts))
        logger.debug("AudioRecorder: round boundary %d at %.2fs", round_number, ts)

    async def stop_recording(self) -> RecordingResult:
        """停止录音，关闭文件，生成切片元数据，返回 RecordingResult。"""
        if not self._is_recording:
            return RecordingResult(
                session_id=self._session_id,
                full_candidate_path=self._candidate_path,
                full_interviewer_path=self._interviewer_path,
                round_slices=[],
                total_duration_sec=0.0,
            )

        self._is_recording = False
        total_duration = time.monotonic() - self._start_time

        # Close files
        if self._candidate_file:
            await self._candidate_file.close()
            self._candidate_file = None
        if self._interviewer_file:
            await self._interviewer_file.close()
            self._interviewer_file = None

        # Rewrite WAV headers with actual data sizes
        await self._rewrite_header(self._candidate_path, self._candidate_bytes)
        await self._rewrite_header(self._interviewer_path, self._interviewer_bytes)

        # Generate round slice metadata
        round_slices = await self._generate_round_slices(total_duration)

        logger.info(
            "AudioRecorder: stopped session=%s duration=%.1fs rounds=%d",
            self._session_id,
            total_duration,
            len(round_slices),
        )
        return RecordingResult(
            session_id=self._session_id,
            full_candidate_path=self._candidate_path,
            full_interviewer_path=self._interviewer_path,
            round_slices=round_slices,
            total_duration_sec=total_duration,
        )

    # ── internals ─────────────────────────────────────────────────────────────

    async def _rewrite_header(self, path: str, data_bytes: int) -> None:
        """Overwrite the WAV header at the start of the file with the correct data size."""
        try:
            async with aiofiles.open(path, "r+b") as f:
                await f.seek(0)
                await f.write(_wav_header(data_bytes))
        except Exception:
            logger.exception("AudioRecorder: failed to rewrite header for %s", path)

    async def _generate_round_slices(self, total_duration: float) -> list[RoundSlice]:
        """Create placeholder WAV files for each round slice and return metadata."""
        if not self._round_boundaries or self._session_dir is None:
            return []

        rounds_dir = self._session_dir / "rounds"
        slices: list[RoundSlice] = []

        boundaries = self._round_boundaries + [(-1, total_duration)]
        prev_time = 0.0

        for i, (round_num, end_time) in enumerate(boundaries[:-1]):
            start_time = prev_time
            slice_end = boundaries[i + 1][1]

            cand_path = str(rounds_dir / f"round_{round_num:03d}_candidate.wav")
            intvw_path = str(rounds_dir / f"round_{round_num:03d}_interviewer.wav")

            # Write placeholder WAV files
            for path in (cand_path, intvw_path):
                async with aiofiles.open(path, "wb") as f:
                    await f.write(_wav_header(0))

            slices.append(
                RoundSlice(
                    round_number=round_num,
                    candidate_audio_path=cand_path,
                    interviewer_audio_path=intvw_path,
                    start_time_sec=start_time,
                    end_time_sec=slice_end,
                )
            )
            prev_time = slice_end

        return slices
