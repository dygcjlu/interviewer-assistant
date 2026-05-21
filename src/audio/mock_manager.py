"""MockAudioManager — 用脚本回放替代真实音频采集，仅用于调试。"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from .recorder import AudioRecorder, RecordingResult
from .script_player import ScriptPlayer
from .transcription import TranscriptionManager
from .trigger import SuggestionTrigger
from ..models.session import ConversationRound, InterviewSession

logger = logging.getLogger(__name__)


class MockAudioManager:
    """AudioManager 的调试替代品 — 内部只有 Recorder + ScriptPlayer，无音频采集和 STT。

    接口与 AudioManager 完全相同，可无缝注入 InterviewController。
    """

    def __init__(self, script_path: str, recordings_dir: str = "recordings") -> None:
        self._script_player = ScriptPlayer(script_path)
        self._recorder = AudioRecorder()
        self._recordings_dir = recordings_dir
        self._transcription_manager: TranscriptionManager | None = None

    async def start(
        self,
        session: InterviewSession,
        ws_sender: Callable[[dict], Awaitable[None]],
        suggestion_trigger: SuggestionTrigger,
        on_round_finalized: Callable[[ConversationRound], Awaitable[None]] | None = None,
    ) -> None:
        self._transcription_manager = TranscriptionManager(
            session=session,
            ws_sender=ws_sender,
            suggestion_trigger=suggestion_trigger,
            recorder=self._recorder,
            on_round_finalized=on_round_finalized,
        )
        await self._recorder.start_recording(session.id, self._recordings_dir)
        await self._script_player.start(self._transcription_manager)
        logger.info("MockAudioManager: started for session=%s", session.id)

    async def stop(self) -> RecordingResult:
        await self._script_player.stop()
        if self._transcription_manager:
            await self._transcription_manager.flush_pending_round()
        result = await self._recorder.stop_recording()
        self._transcription_manager = None
        logger.info("MockAudioManager: stopped")
        return result

    @property
    def transcription_manager(self) -> TranscriptionManager | None:
        return self._transcription_manager
