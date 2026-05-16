"""Orchestrator — Agent 调度器，管理会话生命周期与 Agent 自由切换。"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Awaitable, Callable, AsyncIterator

from .base import AgentRequest, AgentResponse, BaseAgent
from .eval_agent import EvalAgent
from .interview_agent import InterviewAgent
from .resume_agent import ResumeAgent
from ..audio.manager import AudioManager
from ..models.candidate import CandidateProfile
from ..models.exceptions import SessionError
from ..models.session import InterviewSession, InterviewStage, SessionMetadata
from ..storage.memory_module import MemoryModule

logger = logging.getLogger(__name__)


WsSender = Callable[[dict], Awaitable[None]]


_AGENT_TO_STAGE: dict[str, InterviewStage] = {
    "resume": InterviewStage.RESUME_ANALYSIS,
    "interview": InterviewStage.INTERVIEWING,
    "eval": InterviewStage.EVALUATING,
}


def _check_precondition(target: str, session: InterviewSession) -> str | None:
    """返回 None 表示满足前置条件，否则返回错误说明。"""
    if target == "resume":
        return None
    if target == "interview":
        if not session.candidate.id:
            return "切换到面试 Agent 前需先完成候选人信息确认"
        return None
    if target == "eval":
        if len(session.rounds) < 1:
            return "至少需要 1 轮对话记录才能生成评价"
        return None
    return f"未知目标 Agent: {target!r}"


async def _noop_ws_sender(_msg: dict) -> None:
    return None


async def _broadcast(senders: dict[int, WsSender], msg: dict) -> None:
    """向所有已注册客户端广播消息。"""
    for sender in list(senders.values()):
        try:
            await sender(msg)
        except Exception:
            pass


class Orchestrator:
    """Agent 调度器 — 维护 InterviewSession、Agent 切换及相应资源副作用。"""

    def __init__(
        self,
        resume_agent: ResumeAgent,
        interview_agent: InterviewAgent,
        eval_agent: EvalAgent,
        memory_module: MemoryModule,
        audio_manager: AudioManager,
    ) -> None:
        self._agents: dict[str, BaseAgent] = {
            "resume": resume_agent,
            "interview": interview_agent,
            "eval": eval_agent,
        }
        self._memory = memory_module
        self._audio = audio_manager
        self._session: InterviewSession | None = None
        self._active_agent_name: str | None = None
        self._ws_senders: dict[int, WsSender] = {}

    @property
    def _ws_sender(self) -> WsSender:
        """合并广播 sender，供 InterviewAgent 使用。"""
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
        self._active_agent_name = None
        logger.info("Orchestrator: created session %s", session.id)
        return session

    async def get_session(self) -> InterviewSession | None:
        return self._session

    async def close_session(self) -> None:
        if self._session is None:
            return
        if self._active_agent_name is not None:
            try:
                await self._agents[self._active_agent_name].on_deactivate(self._session)
            except Exception:
                logger.exception(
                    "Orchestrator: on_deactivate failed for %s",
                    self._active_agent_name,
                )

        self._session.stage = InterviewStage.COMPLETED
        self._session.metadata.end_time = datetime.now()

        # Sync context summary from InterviewAgent before persisting
        interview_agent = self._agents.get("interview")
        if isinstance(interview_agent, InterviewAgent) and interview_agent.context_manager is not None:
            self._session.context_summary = interview_agent.context_manager.summary

        try:
            await self._memory.save_interview(self._session)
        except Exception:
            logger.exception("Orchestrator: save_interview failed")

        if isinstance(interview_agent, InterviewAgent) and interview_agent.context_manager is not None:
            try:
                await interview_agent.context_manager.reset()
            except Exception:
                logger.exception("Orchestrator: context_manager.reset() failed")

        logger.info("Orchestrator: closed session %s", self._session.id)
        self._session = None
        self._active_agent_name = None

    # ── agent switching ───────────────────────────────────────────────────────

    async def switch_agent(
        self,
        target: str,
        ws_sender: WsSender | None = None,
    ) -> None:
        if self._session is None:
            raise SessionError("当前没有活跃会话")
        if target not in self._agents:
            raise SessionError(f"未知 Agent: {target!r}")

        precondition_err = _check_precondition(target, self._session)
        if precondition_err is not None:
            raise SessionError(precondition_err)

        if ws_sender is not None:
            self._ws_senders[id(ws_sender)] = ws_sender

        # Deactivate current agent (with associated side effects)
        if self._active_agent_name is not None:
            old_name = self._active_agent_name
            try:
                await self._agents[old_name].on_deactivate(self._session)
            except Exception:
                logger.exception("Orchestrator: on_deactivate %s failed", old_name)

            if old_name == "interview":
                try:
                    if target == "eval":
                        await self._audio.stop()
                    else:
                        await self._audio.pause()
                except Exception:
                    logger.warning("Orchestrator: audio shutdown failed", exc_info=True)

        # Activate target agent (with associated side effects)
        new_agent = self._agents[target]
        await new_agent.on_activate(self._session)

        if target == "interview":
            interview_agent = self._agents["interview"]
            assert isinstance(interview_agent, InterviewAgent)
            broadcast = self._ws_sender
            interview_agent.attach_ws_sender(broadcast)
            trigger = interview_agent.suggestion_trigger
            if trigger is not None:
                try:
                    on_round_finalized = (
                        interview_agent.context_manager.add_round
                        if interview_agent.context_manager is not None
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
                        "Orchestrator: audio start failed (continuing without audio)",
                        exc_info=True,
                    )

        self._active_agent_name = target
        self._session.stage = _AGENT_TO_STAGE.get(target, InterviewStage.IDLE)
        logger.info(
            "Orchestrator: switched to agent=%s stage=%s",
            target,
            self._session.stage,
        )

    @property
    def transcription_manager(self):
        """Active TranscriptionManager (available while interview audio is running)."""
        return self._audio.transcription_manager

    def attach_ws_sender(self, ws_sender: WsSender) -> None:
        """注册新的 WebSocket 连接推送回调（广播到所有已连接客户端）。"""
        self._ws_senders[id(ws_sender)] = ws_sender
        interview_agent = self._agents.get("interview")
        if isinstance(interview_agent, InterviewAgent):
            interview_agent.attach_ws_sender(self._ws_sender)

    def detach_ws_sender(self, conn_id: int) -> None:
        """移除指定连接的推送回调。"""
        self._ws_senders.pop(conn_id, None)
        interview_agent = self._agents.get("interview")
        if isinstance(interview_agent, InterviewAgent):
            if self._ws_senders:
                interview_agent.attach_ws_sender(self._ws_sender)
            else:
                interview_agent.attach_ws_sender(_noop_ws_sender)

    @property
    def active_agent(self) -> BaseAgent | None:
        if self._active_agent_name is None:
            return None
        return self._agents.get(self._active_agent_name)

    @property
    def active_agent_name(self) -> str | None:
        return self._active_agent_name

    @property
    def stage(self) -> InterviewStage:
        if self._session is None:
            return InterviewStage.IDLE
        return self._session.stage

    # ── request routing ───────────────────────────────────────────────────────

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        agent = self.active_agent
        if agent is None:
            return AgentResponse(success=False, error="当前没有活跃 Agent")
        return await agent.handle_request(request)

    async def handle_stream(
        self, request: AgentRequest
    ) -> AsyncIterator[str]:
        agent = self.active_agent
        if agent is None:
            return
        async for token in agent.handle_stream(request):
            yield token