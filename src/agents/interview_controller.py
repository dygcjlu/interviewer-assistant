"""InterviewController — 面试状态机控制器。

只负责面试会话生命周期、音频管道管理、WebSocket 广播和阶段状态追踪。
对话路由由 MainAgent 负责。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from src.logging import bind_op, bind_session_id

from ..audio.manager import AudioManager
from ..models.candidate import CandidateProfile
from ..models.exceptions import SessionError
from ..models.session import InterviewSession, InterviewStage, SessionMetadata
from ..storage.memory_module import MemoryModule
from .eval_agent import EvalAgent
from .interview_agent import InterviewAgent

logger = logging.getLogger(__name__)

WsSender = Callable[[dict], Awaitable[None]]


async def _noop_ws_sender(_msg: dict) -> None:
    return None


async def _broadcast(senders: dict[int, WsSender], msg: dict) -> None:
    """向所有 ws_sender 广播；L4-6: 失败的 sender 主动从 dict 中移除（避免持续 push 浪费 CPU）。"""
    dead: list[int] = []
    for sid, sender in list(senders.items()):
        try:
            await sender(msg)
        except Exception as exc:
            logger.info(
                "ws_sender broadcast dropped sid=%d type=%s err=%s",
                sid,
                msg.get("type"),
                exc,
            )
            dead.append(sid)
    for sid in dead:
        senders.pop(sid, None)


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
        # 保护 _session 状态切换：create / close / start / stop 互斥
        self._state_lock = asyncio.Lock()
        self._ws_senders: dict[int, WsSender] = {}

    @property
    def _ws_sender(self) -> WsSender:
        senders = self._ws_senders

        async def _broadcast_sender(msg: dict) -> None:
            await _broadcast(senders, msg)

        return _broadcast_sender

    # ── session lifecycle ─────────────────────────────────────────────────────

    async def create_session(self, candidate_id: str | None = None) -> InterviewSession:
        """创建新会话（受 `_state_lock` 串行化保护）。"""
        async with self._state_lock:
            return await self._create_session_impl(candidate_id)

    async def _create_session_impl(
        self, candidate_id: str | None = None
    ) -> InterviewSession:
        candidate: CandidateProfile
        if candidate_id:
            existing = await self._memory.get_candidate(candidate_id)
            if existing is not None:
                candidate = existing
                try:
                    history = await self._memory.get_candidate_history(candidate_id)
                    if history is not None:
                        candidate.history_summary = history.history_summary
                except Exception:
                    logger.exception(
                        "create_session: get_candidate_history failed, skipping"
                    )
                resume_content = await self._memory.get_resume_markdown(candidate_id)
                candidate.resume_content = resume_content
            else:
                raise SessionError(f"候选人不存在：{candidate_id}")
        else:
            candidate = CandidateProfile(id=str(uuid.uuid4()), name="")

        interview_brief = ""
        if candidate_id:
            interview_brief = self._memory.get_brief(candidate_id)

        session = InterviewSession(
            id=str(uuid.uuid4()),
            candidate=candidate,
            rounds=[],
            stage=InterviewStage.IDLE,
            context_summary="",
            interview_brief=interview_brief,
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
        """关闭当前会话（受 `_state_lock` 保护）。"""
        async with self._state_lock:
            await self._close_session_impl()

    async def _close_session_impl(self) -> None:
        if self._session is None:
            return

        session_id = self._session.id
        try:
            # 兜底：若 close 被直接调用（如 lifespan shutdown 跳过 stop_interview），
            # 必须显式释放 audio 资源（WASAPI / STT WebSocket）
            if self._session.stage == InterviewStage.INTERVIEWING:
                try:
                    await self._audio.stop()
                except Exception:
                    logger.warning(
                        "InterviewController: audio stop in close_session failed",
                        exc_info=True,
                    )

            # Deactivate interview agent if active
            if self._session.stage == InterviewStage.INTERVIEWING:
                try:
                    await self._interview_agent.on_deactivate(self._session)
                except Exception:
                    logger.exception(
                        "InterviewController: on_deactivate interview failed"
                    )

            self._session.stage = InterviewStage.COMPLETED
            self._session.metadata.end_time = datetime.now()

            # Sync context summary
            if self._interview_agent.context_manager is not None:
                self._session.context_summary = (
                    self._interview_agent.context_manager.summary
                )

            try:
                await self._memory.finish_interview(self._session)
            except Exception:
                logger.exception("InterviewController: finish_interview failed")

            if self._interview_agent.context_manager is not None:
                try:
                    await self._interview_agent.context_manager.reset()
                except Exception:
                    logger.exception(
                        "InterviewController: context_manager.reset() failed"
                    )

            logger.info("InterviewController: closed session %s", session_id)
        finally:
            # L5-5: 无论清理是否成功都置 None，防止跨会话状态污染。
            # 意外异常会向上传播，但 _session 引用已释放。
            self._session = None

    # ── interview start/stop ──────────────────────────────────────────────────

    async def start_interview(self) -> None:
        """开始面试（受 `_state_lock` 保护，避免与 stop/close/create 竞态）。"""
        async with self._state_lock:
            await self._start_interview_impl()

    async def _start_interview_impl(self) -> None:
        if self._session is None:
            raise SessionError("当前没有活跃会话")
        if self._session.stage != InterviewStage.IDLE:
            # L3-4 / S-13: 重入时 raise 而非静默 return，让 routes 层返回 409 给前端，
            # 前端应在收到 409 后禁用"开始面试"按钮（防抖）。
            raise SessionError(
                f"当前会话状态为 {self._session.stage.value}，无法开始面试（仅 IDLE 状态允许）"
            )
        if not self._session.candidate.id:
            raise SessionError("切换到面试前需先确认候选人信息")

        bind_op("start_interview")
        bind_session_id(self._session.id)
        start = time.perf_counter()
        logger.info("start_interview begin session_id=%s", self._session.id)

        await self._interview_agent.on_activate(self._session)

        # Register compression callback so context_summary stays current mid-session
        # L3-2 / M5-3: 通过公开 setter 注入，避免直接 setattr 私有属性
        session_ref = self._session
        if self._interview_agent.context_manager is not None:
            self._interview_agent.context_manager.set_compress_done_handler(
                lambda summary: setattr(session_ref, "context_summary", summary)
            )

        broadcast = self._ws_sender
        self._interview_agent.attach_ws_sender(broadcast)
        trigger = self._interview_agent.suggestion_trigger
        if trigger is not None:
            try:
                cm = self._interview_agent.context_manager
                memory_ref = self._memory
                candidate_id_ref = self._session.candidate.id
                interview_id_ref = self._session.id

                async def _on_round_finalized(round_) -> None:
                    """每轮 finalize 后：①更新 ContextManager 短记忆；②append 到 rounds.jsonl WAL。"""
                    if cm is not None:
                        try:
                            await cm.add_round(round_)
                        except Exception:
                            logger.exception(
                                "InterviewController: context_manager.add_round failed"
                            )
                    try:
                        await memory_ref.append_round(
                            candidate_id_ref, interview_id_ref, round_
                        )
                    except Exception:
                        logger.exception(
                            "InterviewController: append_round (WAL) failed session_id=%s round=%d",
                            interview_id_ref,
                            getattr(round_, "round_number", -1),
                        )

                await self._audio.start(
                    session=self._session,
                    ws_sender=broadcast,
                    suggestion_trigger=trigger,
                    on_round_finalized=_on_round_finalized,
                )
                tm = self._audio.transcription_manager
                if tm is not None:
                    self._interview_agent.set_current_round_getter(
                        tm.get_current_round_text
                    )
                # L3-3: 音频启动成功也推一条 ok 状态，便于 UI 重置 badge
                if broadcast is not None:
                    try:
                        await broadcast({"type": "audio_status", "ok": True})
                    except Exception:
                        logger.debug("audio_status ok broadcast failed", exc_info=True)
            except Exception as audio_exc:
                logger.warning(
                    "InterviewController: audio start failed (continuing without audio)",
                    exc_info=True,
                )
                # L3-3: 音频降级时通过 WS 推送 audio_status 让 UI 显示红色 badge
                if broadcast is not None:
                    try:
                        await broadcast(
                            {
                                "type": "audio_status",
                                "ok": False,
                                "reason": audio_exc.__class__.__name__,
                                "message": (
                                    f"音频启动失败（{audio_exc.__class__.__name__}），"
                                    f"已降级为仅手动输入模式。请检查麦克风/STT 配置。"
                                ),
                            }
                        )
                    except Exception:
                        logger.debug(
                            "audio_status fail broadcast failed", exc_info=True
                        )

        self._session.stage = InterviewStage.INTERVIEWING

        try:
            await self._memory.start_interview(self._session)
        except Exception:
            logger.exception("InterviewController: start_interview memory write failed")

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("start_interview done elapsed_ms=%.1f", elapsed_ms)

    async def stop_interview(self) -> None:
        """停止面试（受 `_state_lock` 保护）。"""
        async with self._state_lock:
            await self._stop_interview_impl()

    async def _stop_interview_impl(self) -> None:
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
            self._session.metadata.recording_interviewer_path = (
                rec.full_interviewer_path
            )
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
