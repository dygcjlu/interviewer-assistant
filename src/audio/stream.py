"""AudioStreamBridge — 桥接 AudioCapturer 回调到双 STT 实例 + 录音器。"""
from __future__ import annotations

import logging

from .protocol import AudioFrame, STTEngine
from .recorder import AudioRecorder

logger = logging.getLogger(__name__)


class AudioStreamBridge:
    """桥接 AudioCapturer 的 on_frame 回调到双 STT 实例 + 录音器。"""

    def __init__(
        self,
        candidate_stt: STTEngine,
        interviewer_stt: STTEngine,
        recorder: AudioRecorder,
    ) -> None:
        self._candidate_stt = candidate_stt
        self._interviewer_stt = interviewer_stt
        self._recorder = recorder
        self._stopped = False

    async def on_frame(self, frame: AudioFrame) -> None:
        """按 source 分流到对应 STT + 录音。"""
        if self._stopped:
            return
        await self._recorder.on_audio_frame(frame)
        if frame.source == "candidate":
            await self._candidate_stt.send_audio(frame.data)
        elif frame.source == "interviewer":
            await self._interviewer_stt.send_audio(frame.data)
        # "mixed" source goes only to recorder (both STT instances shouldn't get mixed)

    async def stop(self) -> None:
        """停止桥接（不负责关闭 STT/Recorder，由上层管理）。"""
        self._stopped = True
        logger.debug("AudioStreamBridge: stopped")