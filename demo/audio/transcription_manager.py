"""Transcription manager: bridges AudioStreamManager with BaiduRealtimeSTT."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from demo.audio.capture import CaptureMode
from demo.audio.stream import AudioStreamManager
from demo.audio.stt import BAIDU_CHUNK_BYTES, BaiduRealtimeSTT, TranscriptSegment

logger = logging.getLogger(__name__)

_SENTINEL = None


class TranscriptionManager:
    """Integrates audio capture and Baidu real-time STT.

    One BaiduRealtimeSTT instance is created per audio source.
    Audio frames from AudioStreamManager are chunked and forwarded
    to the corresponding STT WebSocket. Results are merged into a
    single async queue consumed by transcript_stream().

    Usage::

        manager = TranscriptionManager(
            mode=CaptureMode.REMOTE,
            appid=121087443,
            appkey="eLpyLuxR0of5RWsn497uSdp0",
        )
        await manager.start()

        async for segment in manager.transcript_stream():
            print(segment.source, segment.text, segment.is_final)

        await manager.stop()
    """

    def __init__(
        self,
        mode: CaptureMode = CaptureMode.REMOTE,
        appid: int = 0,
        appkey: str = "",
        dev_pid: int = 15372,
    ) -> None:
        self._mode = mode
        self._appid = appid
        self._appkey = appkey
        self._dev_pid = dev_pid

        self._stream_manager = AudioStreamManager(mode=mode, enable_vad=False)
        self._stt_clients: dict[str, BaiduRealtimeSTT] = {}
        self._transcript_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        """Start audio capture and connect all STT WebSocket sessions."""
        sources = (
            ["candidate", "interviewer"]
            if self._mode == CaptureMode.REMOTE
            else ["mixed"]
        )

        self._stream_manager.start()

        for source in sources:
            stt = BaiduRealtimeSTT(
                appid=self._appid,
                appkey=self._appkey,
                dev_pid=self._dev_pid,
                source_label=source,
            )
            await stt.connect()
            self._stt_clients[source] = stt

            self._tasks.append(
                asyncio.create_task(
                    self._feed_audio(source, stt), name=f"feed-{source}"
                )
            )
            self._tasks.append(
                asyncio.create_task(self._recv_loop(stt), name=f"recv-{source}")
            )

        self._running = True
        logger.info(
            "TranscriptionManager started (mode=%s, sources=%s)",
            self._mode.value,
            sources,
        )

    async def transcript_stream(self) -> AsyncIterator[TranscriptSegment]:
        """Async iterator that yields merged transcript segments from all sources."""
        while True:
            segment = await self._transcript_queue.get()
            if segment is _SENTINEL:
                break
            yield segment  # type: ignore[misc]

    async def stop(self) -> None:
        """Stop audio capture, close STT connections, cancel background tasks."""
        self._running = False
        self._stream_manager.stop()

        for stt in self._stt_clients.values():
            await stt.close()
        self._stt_clients.clear()

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        await self._transcript_queue.put(_SENTINEL)
        logger.info("TranscriptionManager stopped")

    async def _feed_audio(self, source: str, stt: BaiduRealtimeSTT) -> None:
        """Read frames from AudioStreamManager, chunk to 160ms, send to STT."""
        try:
            async for frame in self._stream_manager.stream(source):
                pcm = frame.to_int16_bytes()
                offset = 0
                while offset < len(pcm):
                    chunk = pcm[offset : offset + BAIDU_CHUNK_BYTES]
                    await stt.send_audio(chunk)
                    offset += BAIDU_CHUNK_BYTES
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("_feed_audio error (source=%s)", source)

    async def _recv_loop(self, stt: BaiduRealtimeSTT) -> None:
        """Receive segments from one STT client and enqueue them."""
        try:
            async for segment in stt.receive_loop():
                await self._transcript_queue.put(segment)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("_recv_loop error (source=%s)", stt._source_label)
