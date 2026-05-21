"""转写管理器 — STT 结果的分发与轮次管理。"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .protocol import TranscriptSegment
from .recorder import AudioRecorder
from .trigger import SuggestionTrigger
from ..models.session import ConversationRound, InterviewSession

logger = logging.getLogger(__name__)

_SILENCE_TIMEOUT_SEC = 60.0


class TranscriptionManager:
    """STT 结果的分发与轮次管理。"""

    def __init__(
        self,
        session: InterviewSession,
        ws_sender: Callable[[dict], Awaitable[None]],
        suggestion_trigger: SuggestionTrigger,
        recorder: AudioRecorder,
        on_round_finalized: Callable[[ConversationRound], Awaitable[None]] | None = None,
    ) -> None:
        self._session = session
        self._ws_sender = ws_sender
        self._suggestion_trigger = suggestion_trigger
        self._recorder = recorder
        self._on_round_finalized = on_round_finalized

        self._round_number: int = 1
        self._interviewer_text: str = ""
        self._candidate_text: str = ""

        self._silence_task: asyncio.Task | None = None

    # ── public interface ──────────────────────────────────────────────────────

    async def on_segment(self, segment: TranscriptSegment) -> None:
        """接收 STT segment，推送 WS，分发到各消费者。"""
        # 1. Push WebSocket
        try:
            await self._ws_sender(
                {
                    "type": "transcript",
                    "source": segment.source,
                    "text": segment.text,
                    "is_final": segment.is_final,
                }
            )
        except Exception:
            logger.exception("TranscriptionManager: ws_sender failed")

        if not segment.is_final:
            return

        # 2. Reset silence timeout
        self._reset_silence_timer()

        # 3. Accumulate text + trigger logic
        if segment.source == "candidate":
            self._candidate_text += (" " if self._candidate_text else "") + segment.text
            self._suggestion_trigger.on_candidate_segment(segment)

        elif segment.source == "interviewer":
            if self._candidate_text:
                # Candidate already answered → new round starting
                await self.finalize_round()
            self._interviewer_text += (" " if self._interviewer_text else "") + segment.text

    def get_current_round_text(self) -> tuple[str, str]:
        """Returns (interviewer_text, candidate_text) for the current round."""
        return self._interviewer_text, self._candidate_text

    def has_pending_round(self) -> bool:
        """当前轮次是否有尚未归档的转写内容。"""
        return bool(self._interviewer_text or self._candidate_text)

    async def flush_pending_round(self) -> ConversationRound | None:
        """若有未归档内容则结束当前轮次，否则无操作。"""
        if not self.has_pending_round():
            logger.debug("flush_pending_round skipped session_id=%s (no pending)", self._session.id)
            return None
        logger.info("flush_pending_round start session_id=%s", self._session.id)
        return await self.finalize_round()

    async def finalize_round(self) -> ConversationRound:
        """结束当前轮次，归档到 session.rounds，重置累积器。"""
        round_ = ConversationRound(
            round_number=self._round_number,
            interviewer_text=self._interviewer_text,
            candidate_text=self._candidate_text,
        )
        self._session.rounds.append(round_)
        self._recorder.mark_round_boundary(self._round_number)
        logger.info(
            "round_finalized round=%d ivr=%s cand=%s",
            self._round_number,
            self._interviewer_text[:100],
            self._candidate_text[:100],
        )
        self._round_number += 1
        self._interviewer_text = ""
        self._candidate_text = ""
        self._cancel_silence_timer()
        if self._on_round_finalized is not None:
            try:
                await self._on_round_finalized(round_)
            except Exception:
                logger.exception("TranscriptionManager: on_round_finalized callback failed")
        try:
            await self._ws_sender(
                {
                    "type": "session_snapshot",
                    "session_id": self._session.id,
                    "stage": self._session.stage.value,
                    "trigger_mode": self._session.metadata.trigger_mode,
                    "rounds_count": len(self._session.rounds),
                    "candidate_name": self._session.candidate.name or "",
                }
            )
        except Exception:
            logger.debug("TranscriptionManager: session_snapshot broadcast failed")
        return round_

    # ── internals ─────────────────────────────────────────────────────────────

    def _reset_silence_timer(self) -> None:
        self._cancel_silence_timer()
        try:
            loop = asyncio.get_running_loop()
            self._silence_task = loop.create_task(self._silence_timeout())
        except RuntimeError:
            pass

    def _cancel_silence_timer(self) -> None:
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        self._silence_task = None

    async def _silence_timeout(self) -> None:
        await asyncio.sleep(_SILENCE_TIMEOUT_SEC)
        if self._candidate_text:
            logger.warning(
                "TranscriptionManager: silence timeout — force-finalizing round %d",
                self._round_number,
            )
            await self.finalize_round()