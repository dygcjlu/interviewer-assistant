from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Callable, Protocol


@dataclass
class AudioFrame:
    data: bytes
    source: str                            # "candidate" | "interviewer" | "mixed"
    timestamp: float


class AudioCapturer(Protocol):
    """音频采集抽象接口。当前实现：WasapiCapturer（Windows）。"""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def set_on_frame(self, callback: Callable[[AudioFrame], None]) -> None: ...

    @property
    def is_running(self) -> bool: ...


@dataclass
class TranscriptSegment:
    text: str
    source: str                            # "candidate" | "interviewer"
    is_final: bool
    timestamp: datetime
    start_time: float | None = None
    end_time: float | None = None


class STTEngine(Protocol):
    """语音转文字抽象接口。当前实现：BaiduRealtimeSTT | XunfeiRealtimeSTT | VolcRealtimeSTT。"""

    async def connect(self) -> None: ...
    async def send_audio(self, audio_data: bytes) -> None: ...
    def receive(self) -> AsyncIterator[TranscriptSegment]: ...
    async def close(self) -> None: ...
