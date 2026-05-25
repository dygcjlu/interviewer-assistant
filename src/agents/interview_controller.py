"""InterviewController — 面试状态机控制器。

只负责面试会话生命周期、音频管道管理、WebSocket 广播和阶段状态追踪。
对话路由由 MainAgent 负责。
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Awaitable, Callable

from src.logging import bind_agent, bind_op, bind_session_id

from .interview_agent import InterviewAgent
from .eval_agent import EvalAgent
from ..audio.manager import AudioManager
from ..models.candidate import CandidateProfile
from ..models.exceptions import SessionError
from ..models.session import InterviewSession, InterviewStage, SessionMetadata
from ..storage.memory_module import MemoryModule

logger = logging.getLogger(__name__)

WsSender = Callable[[dict], Awaitable[None]]


async def _noop_ws_sender(_msg: dict) -> None:
    return None


async def _broadcast(senders: dict[int, WsSender], msg: dict) -> None:
    for sender in list(senders.values()):
        try:
            await sender(msg)
        except Exception:
            pass


class InterviewController:
    """面试状态机控制器 — 管理 InterviewSession 生命周期与音频管道。

    状态机：
        idle → interviewing (start_interview)
             → evaluating (stop_interview)
             → completed (close_session)
    """

    def __init__(
        self,
        interview_agent: InterviewAgent,
        eval_agent: EvalAgent,
        memory_module: MemoryModule,
        audio_manager: AudioManager,
    ) -> None:
        self._interview_agent = interview_agent
        self._eval_agent = eval_agent
        self._memory = memory_module
        self._audio = audio_manager
        self._session: InterviewSession | None = None
        self._ws_senders: dict[int, WsSender] = {}

    @property
    def _ws_sender(self) -> WsSender:
        senders = self._ws_senders

        async def _broadcast_sender(msg: dict) -> None:
            await _broadcast(senders, msg)

        return _broadcast_sender

    # ── session lifecycle ─────────────────────────────────────────────────────

    async def create_session(
        self, candidate_id: str | None = None
    ) -> InterviewSession:
        candidate: CandidateProfile
        if candidate_id:
            existing = await self._memory.get_candidate(candidate_id)
            if existing is not None:
                candidate = existing
                history = await self._memory.get_candidate_history(candidate_id)
                if history is not None:
                    candidate.history_summary = history.history_summary
                resume_content = await self._memory.get_resume_markdown(candidate_id)
                candidate.resume_content = resume_content
            else:
                candidate = CandidateProfile(id=candidate_id, name="")
        else:
            candidate = CandidateProfile(id=str(uuid.uuid4()), name="")

        session = InterviewSession(
            id=str(uuid.uuid4()),
            candidate=candidate,
            question_plan=[],
            rounds=[],
            stage=InterviewStage.IDLE,
            context_summary="",
            covered_dimensions=set(),
            working_notes="",
            metadata=SessionMetadata(
                candidate_id=candidate.id,
                start_time=datetime.now(),
            ),
        )
        self._session = session
        bind_session_id(session.id)
        logger.info(
            "create_session done session_id=%s candidate_id=%s",
            session.id,
            candidate.id,
        )
        return session

    async def get_session(self) -> InterviewSession | None:
        return self._session

    async def close_session(self) -> None:
        if self._session is None:
            return

        # Deactivate interview agent if active
        if self._session.stage == InterviewStage.INTERVIEWING:
            try:
                await self._interview_agent.on_deactivate(self._session)
            except Exception:
                logger.exception("InterviewController: on_deactivate interview failed")

        self._session.stage = InterviewStage.COMPLETED
        self._session.metadata.end_time = datetime.now()

        # Sync context summary
        if self._interview_agent.context_manager is not None:
            self._session.context_summary = self._interview_agent.context_manager.summary

        try:
            await self._memory.finish_interview(self._session)
        except Exception:
            logger.exception("InterviewController: finish_interview failed")

        if self._interview_agent.context_manager is not None:
            try:
                await self._interview_agent.context_manager.reset()
            except Exception:
                logger.exception("InterviewController: context_manager.reset() failed")

        logger.info("InterviewController: closed session %s", self._session.id)
        self._session = None

    # ── interview start/stop ──────────────────────────────────────────────────

    async def start_interview(self) -> None:
        if self._session is None:
            raise SessionError("当前没有活跃会话")
        if self._session.stage == InterviewStage.INTERVIEWING:
            logger.warning(
                "start_interview called but session already interviewing, ignoring session_id=%s",
                self._session.id,
            )
            return
        if not self._session.candidate.id:
            raise SessionError("切换到面试前需先确认候选人信息")

        bind_op("start_interview")
        bind_session_id(self._session.id)
        start = time.perf_counter()
        logger.info("start_interview begin session_id=%s", self._session.id)

        await self._interview_agent.on_activate(self._session)

        # Register compression callback so context_summary stays current mid-session
        session_ref = self._session
        if self._interview_agent.context_manager is not None:
            self._interview_agent.context_manager._on_compress_done = (
                lambda summary: setattr(session_ref, "context_summary", summary)
            )

        broadcast = self._ws_sender
        self._interview_agent.attach_ws_sender(broadcast)
        trigger = self._interview_agent.suggestion_trigger
        if trigger is not None:
            try:
                on_round_finalized = (
                    self._interview_agent.context_manager.add_round
                    if self._interview_agent.context_manager is not None
                    else None
                )
                await self._audio.start(
                    session=self._session,
                    ws_sender=broadcast,
                    suggestion_trigger=trigger,
                    on_round_finalized=on_round_finalized,
                )
            except Exception:
                logger.warning(
                    "InterviewController: audio start failed (continuing without audio)",
                    exc_info=True,
                )

        self._session.stage = InterviewStage.INTERVIEWING

        try:
            await self._memory.start_interview(self._session)
        except Exception:
            logger.exception("InterviewController: start_interview memory write failed")

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("start_interview done elapsed_ms=%.1f", elapsed_ms)

    async def stop_interview(self) -> None:
        if self._session is None:
            raise SessionError("当前没有活跃会话")

        bind_op("stop_interview")
        bind_session_id(self._session.id)
        start = time.perf_counter()

        # Flush pending transcription round
        tm = self._audio.transcription_manager
        if tm is not None:
            try:
                await tm.flush_pending_round()
            except Exception:
                logger.exception("InterviewController: flush_pending_round failed")

        # Deactivate interview agent and stop audio
        try:
            await self._interview_agent.on_deactivate(self._session)
        except Exception:
            logger.exception("InterviewController: on_deactivate failed")

        try:
            rec = await self._audio.stop()
            self._session.metadata.recording_candidate_path = rec.full_candidate_path
            self._session.metadata.recording_interviewer_path = rec.full_interviewer_path
        except Exception:
            logger.warning("InterviewController: audio stop failed", exc_info=True)

        # Trigger eval if we have rounds
        if len(self._session.rounds) >= 1:
            self._session.stage = InterviewStage.EVALUATING
            logger.info(
                "stop_interview: %d rounds, triggering eval", len(self._session.rounds)
            )
        else:
            self._session.stage = InterviewStage.COMPLETED
            logger.info("stop_interview: 0 rounds, skipping eval")

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("stop_interview done elapsed_ms=%.1f", elapsed_ms)

    # ── WebSocket management ──────────────────────────────────────────────────

    @property
    def transcription_manager(self):
        return self._audio.transcription_manager

    def attach_ws_sender(self, ws_sender: WsSender) -> None:
        self._ws_senders[id(ws_sender)] = ws_sender
        self._interview_agent.attach_ws_sender(self._ws_sender)

    def detach_ws_sender(self, conn_id: int) -> None:
        self._ws_senders.pop(conn_id, None)
        if self._ws_senders:
            self._interview_agent.attach_ws_sender(self._ws_sender)
        else:
            self._interview_agent.attach_ws_sender(_noop_ws_sender)

    @property
    def stage(self) -> InterviewStage:
        if self._session is None:
            return InterviewStage.IDLE
        return self._session.stage

    @property
    def interview_agent(self) -> InterviewAgent:
        return self._interview_agent

    @property
    def eval_agent(self) -> EvalAgent:
        return self._eval_agent
