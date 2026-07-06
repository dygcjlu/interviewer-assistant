"""Audio capture service supporting WASAPI Loopback and microphone dual-channel recording."""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import soundcard as sc

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_DURATION_MS = 300
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)


class CaptureMode(enum.Enum):
    REMOTE = "remote"
    LOCAL = "local"


@dataclass
class AudioFrame:
    """A single chunk of captured audio."""

    data: np.ndarray
    sample_rate: int = SAMPLE_RATE
    channels: int = CHANNELS
    source: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_int16_bytes(self) -> bytes:
        """Convert float32 audio data to 16-bit PCM bytes for STT / VAD consumption."""
        clamped = np.clip(self.data, -1.0, 1.0)
        return (clamped * 32767).astype(np.int16).tobytes()


class _CaptureWorker:
    """Runs audio recording in a background thread and forwards frames via callback."""

    def __init__(
        self,
        device: sc._Microphone,
        source_label: str,
        on_frame: Callable[[AudioFrame], None],
    ):
        self._device = device
        self._source_label = source_label
        self._on_frame = on_frame
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            "Capture worker started: %s (loopback=%s)",
            self._source_label,
            getattr(self._device, "isloopback", False),
        )

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("Capture worker stopped: %s", self._source_label)

    def _run(self) -> None:
        try:
            with self._device.recorder(
                samplerate=SAMPLE_RATE, channels=CHANNELS
            ) as recorder:
                while self._running:
                    raw = recorder.record(numframes=FRAME_SAMPLES)
                    frame = AudioFrame(
                        data=raw.flatten().astype(np.float32),
                        source=self._source_label,
                    )
                    self._on_frame(frame)
        except Exception:
            logger.exception("Capture worker error (%s)", self._source_label)
            self._running = False


def _find_loopback_mic_for_speaker(speaker: sc._Speaker) -> sc._Microphone:
    """Find the loopback (virtual) microphone that corresponds to a given speaker."""
    loopback_mics = sc.all_microphones(include_loopback=True)
    for mic in loopback_mics:
        if getattr(mic, "isloopback", False) and speaker.id in mic.id:
            return mic
    # Fallback: return the first loopback mic available
    for mic in loopback_mics:
        if getattr(mic, "isloopback", False):
            return mic
    raise RuntimeError(
        f"No loopback microphone found for speaker '{speaker.name}'. "
        "Ensure your system supports WASAPI loopback recording."
    )


class AudioCapture:
    """High-level audio capture supporting remote (dual-channel) and local (mic-only) modes.

    Remote mode:
        - Loopback channel captures system audio (candidate voice from meeting software).
        - Microphone channel captures the interviewer's voice.
        - Speaker identity is determined by the channel, no diarization needed.

    Local mode:
        - Only the microphone is used, capturing a mixed signal of both speakers.
        - Downstream diarization is required to separate speakers.
    """

    def __init__(
        self,
        mode: CaptureMode = CaptureMode.REMOTE,
        on_frame: Callable[[AudioFrame], None] | None = None,
        loopback_device: sc._Microphone | None = None,
        mic_device: sc._Microphone | None = None,
    ):
        self._mode = mode
        self._on_frame = on_frame or (lambda _: None)
        self._loopback_device = loopback_device
        self._mic_device = mic_device
        self._workers: list[_CaptureWorker] = []
        self._started = False

    @property
    def mode(self) -> CaptureMode:
        return self._mode

    @property
    def is_running(self) -> bool:
        return self._started

    def set_on_frame(self, callback: Callable[[AudioFrame], None]) -> None:
        self._on_frame = callback

    def start(self) -> None:
        if self._started:
            logger.warning("AudioCapture already running")
            return

        if self._mode == CaptureMode.REMOTE:
            self._start_remote()
        else:
            self._start_local()
        self._started = True
        logger.info("AudioCapture started in %s mode", self._mode.value)

    def stop(self) -> None:
        for w in self._workers:
            w.stop()
        self._workers.clear()
        self._started = False
        logger.info("AudioCapture stopped")

    def _start_remote(self) -> None:
        loopback = self._loopback_device
        if loopback is None:
            speaker = sc.default_speaker()
            loopback = _find_loopback_mic_for_speaker(speaker)

        mic = self._mic_device or sc.default_microphone()

        loopback_worker = _CaptureWorker(
            device=loopback,
            source_label="candidate",
            on_frame=self._on_frame,
        )
        mic_worker = _CaptureWorker(
            device=mic,
            source_label="interviewer",
            on_frame=self._on_frame,
        )
        self._workers = [loopback_worker, mic_worker]
        loopback_worker.start()
        mic_worker.start()

    def _start_local(self) -> None:
        mic = self._mic_device or sc.default_microphone()
        mic_worker = _CaptureWorker(
            device=mic,
            source_label="mixed",
            on_frame=self._on_frame,
        )
        self._workers = [mic_worker]
        mic_worker.start()
