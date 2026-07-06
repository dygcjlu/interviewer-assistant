"""MockAudioCapturer — Linux 开发环境替代 WASAPI 的静音音频采集实现。

绝不在生产环境使用；仅供本地开发和单元测试。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from .protocol import AudioFrame, TranscriptSegment


class MockAudioCapturer:
    """返回静音 PCM 数据的音频采集器，替代 Windows WASAPI。

    每 20ms 产出一帧 320 字节（16kHz, 16bit, 单声道）静音数据。
    """

    _FRAME_INTERVAL = 0.02  # 20ms
    _FRAME_BYTES = 320  # 16kHz × 0.02s × 2 bytes

    def __init__(self) -> None:
        self._callback: Callable[[AudioFrame], None] | None = None
        self._running = False
        self._task: asyncio.Task | None = None

    def set_on_frame(self, callback: Callable[[AudioFrame], None]) -> None:
        self._callback = callback

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._emit_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _emit_loop(self) -> None:
        import time

        while self._running:
            if self._callback:
                frame = AudioFrame(
                    data=b"\x00" * self._FRAME_BYTES,
                    source="mixed",
                    timestamp=time.monotonic(),
                )
                self._callback(frame)
            await asyncio.sleep(self._FRAME_INTERVAL)


class MockSTTEngine:
    """返回空结果的 STT 引擎，供单元测试使用。"""

    async def connect(self) -> None:
        pass

    async def send_audio(self, audio_data: bytes) -> None:
        pass

    def receive(self) -> AsyncIterator[TranscriptSegment]:
        # Protocol 要求 receive 是普通 def，返回 AsyncIterator。
        # 内部 async generator 函数确保调用方可以 `async for` 迭代（永不产出片段）。
        async def _empty() -> AsyncIterator[TranscriptSegment]:
            return
            yield  # noqa: F841 — 使 _empty 成为 async generator 而非 coroutine

        return _empty()

    async def close(self) -> None:
        pass
