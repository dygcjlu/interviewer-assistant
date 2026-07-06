"""Audio stream manager: bridges audio capture with downstream consumers (STT, etc.)

Uses asyncio queues to decouple the threaded capture layer from async consumers.
Integrates VAD filtering so that only speech frames are forwarded.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from demo.audio.capture import AudioCapture, AudioFrame, CaptureMode
from demo.audio.vad import VADFilter

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_MAXSIZE = 200


class AudioStreamManager:
    """Manages the full pipeline: capture -> VAD -> async queue -> consumers.

    Usage::

        manager = AudioStreamManager(mode=CaptureMode.REMOTE)
        manager.start()

        async for frame in manager.stream("candidate"):
            pcm = frame.to_int16_bytes()
            # send to STT ...

        manager.stop()
    """

    def __init__(
        self,
        mode: CaptureMode = CaptureMode.REMOTE,
        vad_aggressiveness: int = 2,
        vad_speech_threshold: float = 0.3,
        enable_vad: bool = True,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        capture: AudioCapture | None = None,
    ) -> None:
        self._mode = mode
        self._enable_vad = enable_vad
        self._vad = (
            VADFilter(vad_aggressiveness, vad_speech_threshold) if enable_vad else None
        )
        self._queue_maxsize = queue_maxsize

        self._queues: dict[str, asyncio.Queue[AudioFrame | None]] = {}
        self._global_queue: asyncio.Queue[AudioFrame | None] = asyncio.Queue(
            maxsize=queue_maxsize,
        )
        self._loop: asyncio.AbstractEventLoop | None = None

        self._capture = capture or AudioCapture(mode=mode)
        self._capture.set_on_frame(self._on_frame)

    @property
    def capture(self) -> AudioCapture:
        return self._capture

    def start(self) -> None:
        """Start audio capture and frame dispatching."""
        self._loop = asyncio.get_event_loop()
        self._capture.start()
        logger.info("AudioStreamManager started")

    def stop(self) -> None:
        """Stop capture and signal all consumers to finish."""
        self._capture.stop()
        self._send_sentinel(self._global_queue)
        for q in self._queues.values():
            self._send_sentinel(q)
        logger.info("AudioStreamManager stopped")

    def _send_sentinel(self, queue: asyncio.Queue[AudioFrame | None]) -> None:
        """Put a None sentinel into the queue to signal end-of-stream."""
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(queue.put_nowait, None)
        else:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def _on_frame(self, frame: AudioFrame) -> None:
        """Callback invoked from capture threads. Applies VAD then enqueues."""
        if self._enable_vad and self._vad is not None:
            if not self._vad.is_speech(frame):
                return

        if self._loop is None:
            return

        self._enqueue(self._global_queue, frame)

        source_queue = self._queues.get(frame.source)
        if source_queue is not None:
            self._enqueue(source_queue, frame)

    def _enqueue(
        self, queue: asyncio.Queue[AudioFrame | None], frame: AudioFrame
    ) -> None:
        try:
            self._loop.call_soon_threadsafe(queue.put_nowait, frame)
        except asyncio.QueueFull:
            logger.warning("Audio queue full, dropping frame (source=%s)", frame.source)

    async def stream(self, source: str | None = None) -> AsyncIterator[AudioFrame]:
        """Async iterator that yields speech frames.

        Parameters
        ----------
        source :
            If provided, only yield frames from this source label
            (e.g. "candidate", "interviewer").
            If None, yields frames from all sources.
        """
        if source is None:
            queue = self._global_queue
        else:
            if source not in self._queues:
                self._queues[source] = asyncio.Queue(maxsize=self._queue_maxsize)
            queue = self._queues[source]

        while True:
            frame = await queue.get()
            if frame is None:
                break
            yield frame

    async def get_frame(self, source: str | None = None) -> AudioFrame | None:
        """Get a single frame. Returns None when the stream ends."""
        if source is None:
            queue = self._global_queue
        else:
            if source not in self._queues:
                self._queues[source] = asyncio.Queue(maxsize=self._queue_maxsize)
            queue = self._queues[source]

        frame = await queue.get()
        return frame
