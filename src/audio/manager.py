"""音频子系统统一管理器。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ..models.session import ConversationRound, InterviewSession
from .protocol import AudioCapturer, AudioFrame, STTEngine
from .recorder import AudioRecorder, RecordingResult
from .stream import AudioStreamBridge
from .transcription import TranscriptionManager
from .trigger import SuggestionTrigger

logger = logging.getLogger(__name__)


class AudioManager:
    """音频子系统统一管理器 — 封装所有音频组件的启停协调。"""

    def __init__(
        self,
        capturer: AudioCapturer,
        candidate_stt: STTEngine,
        interviewer_stt: STTEngine,
        recorder: AudioRecorder,
        recordings_dir: str = "recordings",
    ) -> None:
        self._capturer = capturer
        self._candidate_stt = candidate_stt
        self._interviewer_stt = interviewer_stt
        self._recorder = recorder
        self._recordings_dir = recordings_dir

        self._bridge: AudioStreamBridge | None = None
        self._transcription_manager: TranscriptionManager | None = None
        self._candidate_loop_task: asyncio.Task | None = None
        self._interviewer_loop_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(
        self,
        session: InterviewSession,
        ws_sender: Callable[[dict], Awaitable[None]],
        suggestion_trigger: SuggestionTrigger,
        on_round_finalized: (
            Callable[[ConversationRound], Awaitable[None]] | None
        ) = None,
    ) -> None:
        """启动音频采集全链路。"""
        # 1. Create TranscriptionManager
        self._transcription_manager = TranscriptionManager(
            session=session,
            ws_sender=ws_sender,
            suggestion_trigger=suggestion_trigger,
            recorder=self._recorder,
            on_round_finalized=on_round_finalized,
        )

        # 2. Create AudioStreamBridge
        self._bridge = AudioStreamBridge(
            candidate_stt=self._candidate_stt,
            interviewer_stt=self._interviewer_stt,
            recorder=self._recorder,
        )

        # 3. Capture the event loop here (we are in async context). The callback
        # references self._bridge so resume() can swap in a fresh bridge without
        # re-registering the callback on the capturer (WASAPI callback thread).
        self._loop = asyncio.get_running_loop()

        def _sync_frame_callback(frame: AudioFrame) -> None:
            try:
                if self._loop is not None and self._bridge is not None:
                    fut = asyncio.run_coroutine_threadsafe(
                        self._bridge.on_frame(frame), self._loop
                    )

                    def _on_done(f: asyncio.Future) -> None:
                        exc = f.exception() if not f.cancelled() else None
                        if exc is not None:
                            logger.error(
                                "AudioManager: on_frame future exception: %s", exc
                            )

                    fut.add_done_callback(_on_done)
            except Exception:
                logger.exception("AudioManager: frame callback error")

        self._capturer.set_on_frame(_sync_frame_callback)

        try:
            # 4. Connect STT engines
            await self._candidate_stt.connect()
            await self._interviewer_stt.connect()

            # 5. Start STT receive loops
            self._candidate_loop_task = self._loop.create_task(
                self._stt_receive_loop(self._candidate_stt)
            )
            self._interviewer_loop_task = self._loop.create_task(
                self._stt_receive_loop(self._interviewer_stt)
            )

            # 6. Start capturer
            await self._capturer.start()

            # 7. Start recording
            await self._recorder.start_recording(session.id, self._recordings_dir)

        except Exception:
            logger.exception("AudioManager: start failed, rolling back")
            await self._rollback_start()
            raise

        logger.info("AudioManager: started for session=%s", session.id)

    async def _rollback_start(self) -> None:
        """回滚 start() 中已分配的资源（STT 任务、连接、TranscriptionManager）。"""
        try:
            await self._capturer.stop()
        except Exception:
            logger.warning("AudioManager rollback: capturer stop failed", exc_info=True)

        for task in (self._candidate_loop_task, self._interviewer_loop_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._candidate_loop_task = None
        self._interviewer_loop_task = None

        try:
            await self._candidate_stt.close()
        except Exception:
            logger.warning(
                "AudioManager rollback: candidate_stt close failed", exc_info=True
            )
        try:
            await self._interviewer_stt.close()
        except Exception:
            logger.warning(
                "AudioManager rollback: interviewer_stt close failed", exc_info=True
            )

        self._transcription_manager = None
        self._bridge = None

    async def stop(self) -> RecordingResult:
        """有序停止全部组件，返回录音结果。"""
        # 1. Stop capturer
        await self._capturer.stop()

        # 2. Stop bridge
        if self._bridge:
            await self._bridge.stop()

        # 3. Close STT engines
        await self._candidate_stt.close()
        await self._interviewer_stt.close()

        # 4. Cancel receive loop tasks
        for task in (self._candidate_loop_task, self._interviewer_loop_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._candidate_loop_task = None
        self._interviewer_loop_task = None

        # 5. Finalize last round if any content
        if self._transcription_manager:
            _, cand_text = self._transcription_manager.get_current_round_text()
            if cand_text:
                await self._transcription_manager.finalize_round()

        # 6. Stop recording
        result = await self._recorder.stop_recording()
        self._transcription_manager = None
        self._bridge = None
        logger.info("AudioManager: stopped, duration=%.1fs", result.total_duration_sec)
        return result

    # M8-1: pause/resume 已删除（定义但全代码无调用）。当前业务只用 start/stop。

    # ── properties ────────────────────────────────────────────────────────────

    @property
    def transcription_manager(self) -> TranscriptionManager | None:
        """当前 TranscriptionManager 实例（start() 后可用，stop() 后为 None）。"""
        return self._transcription_manager

    # ── internals ─────────────────────────────────────────────────────────────

    async def _stt_receive_loop(self, stt: STTEngine) -> None:
        """后台 task：消费 STT 输出 → 转发到 TranscriptionManager。"""
        try:
            async for segment in stt.receive():
                if self._transcription_manager:
                    await self._transcription_manager.on_segment(segment)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("AudioManager: STT receive loop error")
