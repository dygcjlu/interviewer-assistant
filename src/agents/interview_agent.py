"""InterviewAgent — 实时面试追问建议（流式）。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, TYPE_CHECKING

from src.logging import bind_op, text_summary

from .base import AgentRequest, AgentResponse, BaseAgent
from ..audio.trigger import SuggestionTrigger
from ..framework.prompt_builder import AgentConfig, PromptBuilder
from ..framework.tool_registry import ToolRegistry
from ..models.message import Message
from ..models.session import InterviewSession

if TYPE_CHECKING:
    from ..framework.context import ContextManager
    from ..llm.protocol import LLMClient

logger = logging.getLogger(__name__)

_DEFAULT_SILENCE_SEC = 2.0
_DEFAULT_MIN_INTERVAL_SEC = 5.0


class InterviewAgent(BaseAgent):
    """实时面试 Agent — 持有 SuggestionTrigger，流式输出追问建议。"""

    def __init__(
        self,
        config: AgentConfig,
        prompt_builder: PromptBuilder,
        llm_client: "LLMClient",
        tool_registry: ToolRegistry,
        context_manager: "ContextManager",
        silence_threshold_sec: float = _DEFAULT_SILENCE_SEC,
        min_interval_sec: float = _DEFAULT_MIN_INTERVAL_SEC,
    ) -> None:
        super().__init__(config, prompt_builder, llm_client, tool_registry)
        self.context_manager = context_manager
        self._silence_threshold = silence_threshold_sec
        self._min_interval = min_interval_sec

        self._session: InterviewSession | None = None
        self._suggestion_trigger: SuggestionTrigger | None = None
        self._current_stream_task: asyncio.Task | None = None
        self._request_counter: int = 0
        self._ws_sender = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def on_activate(self, session: InterviewSession) -> None:
        self._session = session
        self._suggestion_trigger = SuggestionTrigger(
            on_trigger=self._on_trigger_fired,
            silence_threshold_sec=self._silence_threshold,
            min_interval_sec=self._min_interval,
        )
        logger.info("InterviewAgent activated for session %s", session.id)

    async def on_deactivate(self, session: InterviewSession) -> None:
        if self._suggestion_trigger is not None:
            self._suggestion_trigger.stop()
            self._suggestion_trigger = None

        if self._current_stream_task and not self._current_stream_task.done():
            self._current_stream_task.cancel()
            try:
                await self._current_stream_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("InterviewAgent: stream task ended with error")
        self._current_stream_task = None
        self._session = None
        logger.info("InterviewAgent deactivated")

    # ── public interface ──────────────────────────────────────────────────────

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        bind_op(request.type)
        start = time.perf_counter()
        logger.info(
            "InterviewAgent handle_request start type=%s session_id=%s",
            request.type,
            request.session.id,
        )

        if request.type == "set_trigger_mode":
            mode = request.payload.get("mode", "auto")
            if self._suggestion_trigger is None:
                resp = AgentResponse(
                    success=False, error="SuggestionTrigger 未初始化（Agent 尚未激活）"
                )
            else:
                try:
                    self._suggestion_trigger.set_mode(mode)
                    resp = AgentResponse(success=True, data={"mode": mode})
                except ValueError as exc:
                    resp = AgentResponse(success=False, error=str(exc))
            elapsed_ms = (time.perf_counter() - start) * 1000
            if resp.success:
                logger.info(
                    "InterviewAgent set_trigger_mode done mode=%s elapsed_ms=%.1f",
                    mode,
                    elapsed_ms,
                )
            else:
                logger.error(
                    "InterviewAgent set_trigger_mode failed mode=%s error=%s elapsed_ms=%.1f",
                    mode,
                    resp.error,
                    elapsed_ms,
                )
            return resp

        if request.type == "trigger_suggestion":
            if self._suggestion_trigger is None:
                resp = AgentResponse(success=False, error="Agent 尚未激活")
                logger.error(
                    "InterviewAgent trigger_suggestion failed error=%s",
                    resp.error,
                )
                return resp
            req_id = self._request_counter
            trigger_mode = (
                self._suggestion_trigger.mode
                if self._suggestion_trigger is not None
                else "unknown"
            )
            logger.info(
                "InterviewAgent trigger_suggestion start request_id=%d rounds_count=%d trigger_mode=%s",
                req_id,
                len(request.session.rounds),
                trigger_mode,
            )
            await self._on_trigger_fired(req_id)
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "InterviewAgent trigger_suggestion accepted request_id=%d status=generating elapsed_ms=%.1f",
                req_id,
                elapsed_ms,
            )
            return AgentResponse(success=True, data={"request_id": req_id, "status": "generating"})

        logger.error("InterviewAgent handle_request unknown type=%r", request.type)
        return AgentResponse(
            success=False, error=f"Unknown request type: {request.type!r}"
        )

    async def handle_stream(self, request: AgentRequest) -> AsyncIterator[str]:
        req_id = request.request_id if request.request_id is not None else self._request_counter
        async for token in self.generate_suggestion(req_id):
            yield token

    async def generate_suggestion(self, request_id: int) -> AsyncIterator[str]:
        """生成追问建议 — SuggestionTrigger 回调 + 手动触发共用入口。"""
        if self._session is None:
            logger.warning("InterviewAgent.generate_suggestion called without active session")
            return

        bind_op("generate_suggestion")
        start = time.perf_counter()
        rounds_count = len(self._session.rounds)
        if self._session.rounds:
            last_round = self._session.rounds[-1]
            context_hint = (
                f"ivr={text_summary(last_round.interviewer_text)} "
                f"cand={text_summary(last_round.candidate_text)}"
            )
        else:
            context_hint = "no_rounds"
        logger.info(
            "InterviewAgent generate_suggestion start request_id=%d rounds_count=%d %s",
            request_id,
            rounds_count,
            context_hint,
        )

        # 取消上一次进行中的流式请求（候选人继续说话 → 旧建议作废）
        if self._current_stream_task and not self._current_stream_task.done():
            self._current_stream_task.cancel()
            try:
                await self._current_stream_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("InterviewAgent: previous stream task error")
        self._current_stream_task = None
        self._request_counter = request_id + 1

        messages = self.prompt_builder.build(self._session, self.config)
        if self._session.rounds:
            last_round = self._session.rounds[-1]
            current_text = (
                f"面试官：{last_round.interviewer_text}\n"
                f"候选人最新回答：{last_round.candidate_text}\n\n"
                "请给出一条追问建议。"
            )
            messages.append(Message(role="user", content=current_text))
        else:
            messages.append(
                Message(role="user", content="面试刚开始，请给出一个开场问题建议。")
            )

        token_count = 0
        prompt_tokens = 0
        completion_tokens = 0
        try:
            stream_iter = self.llm_client.chat_stream(messages)
            async for chunk in stream_iter:
                if chunk.delta:
                    token_count += len(chunk.delta)
                    yield chunk.delta
                if chunk.is_final:
                    prompt_tokens = chunk.prompt_tokens
                    completion_tokens = chunk.completion_tokens
                    break
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "InterviewAgent generate_suggestion done request_id=%d "
                "output_chars=%d prompt_tokens=%d completion_tokens=%d elapsed_ms=%.1f",
                request_id,
                token_count,
                prompt_tokens,
                completion_tokens,
                elapsed_ms,
            )
        except asyncio.CancelledError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "InterviewAgent generate_suggestion cancelled request_id=%d elapsed_ms=%.1f",
                request_id,
                elapsed_ms,
            )
            raise
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "InterviewAgent generate_suggestion failed request_id=%d elapsed_ms=%.1f",
                request_id,
                elapsed_ms,
            )

    @property
    def suggestion_trigger(self) -> SuggestionTrigger | None:
        return self._suggestion_trigger

    # ── internals ─────────────────────────────────────────────────────────────

    async def _on_trigger_fired(self, request_id: int) -> None:
        """SuggestionTrigger 回调：在后台 task 内消费 generate_suggestion 流。"""
        trigger_mode = (
            self._suggestion_trigger.mode if self._suggestion_trigger is not None else "unknown"
        )
        logger.info(
            "InterviewAgent on_trigger_fired request_id=%d trigger_mode=%s has_ws_sender=%s",
            request_id,
            trigger_mode,
            self._ws_sender is not None,
        )
        if self._ws_sender is None:
            logger.warning("InterviewAgent on_trigger_fired without ws_sender")

        async def _runner() -> None:
            try:
                async for token in self.generate_suggestion(request_id):
                    if self._ws_sender is not None:
                        try:
                            await self._ws_sender(
                                {
                                    "type": "suggestion_delta",
                                    "request_id": request_id,
                                    "delta": token,
                                }
                            )
                        except Exception:
                            logger.exception("InterviewAgent: ws_sender failed")
                if self._ws_sender is not None:
                    try:
                        await self._ws_sender(
                            {
                                "type": "suggestion_final",
                                "request_id": request_id,
                            }
                        )
                    except Exception:
                        logger.exception("InterviewAgent: ws_sender final failed")
                logger.info(
                    "InterviewAgent suggestion_stream finished request_id=%d",
                    request_id,
                )
            except asyncio.CancelledError:
                logger.info(
                    "InterviewAgent suggestion_stream cancelled request_id=%d",
                    request_id,
                )

        self._current_stream_task = asyncio.create_task(_runner())

    def attach_ws_sender(self, ws_sender) -> None:
        """由 InterviewController 注入 WebSocket 推送回调（在 on_activate 之后）。"""
        self._ws_sender = ws_sender