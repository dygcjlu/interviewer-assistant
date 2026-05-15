"""InterviewAgent — 实时面试追问建议（流式）。"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, TYPE_CHECKING

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
        if request.type == "set_trigger_mode":
            mode = request.payload.get("mode", "auto")
            if self._suggestion_trigger is None:
                return AgentResponse(
                    success=False, error="SuggestionTrigger 未初始化（Agent 尚未激活）"
                )
            try:
                self._suggestion_trigger.set_mode(mode)
            except ValueError as exc:
                return AgentResponse(success=False, error=str(exc))
            return AgentResponse(success=True, data={"mode": mode})

        if request.type == "trigger_suggestion":
            if self._suggestion_trigger is None:
                return AgentResponse(success=False, error="Agent 尚未激活")
            req_id = self._request_counter
            await self._on_trigger_fired(req_id)
            return AgentResponse(success=True, data={"request_id": req_id, "status": "generating"})

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

        try:
            stream_iter = self.llm_client.chat_stream(messages)
            async for chunk in stream_iter:
                if chunk.delta:
                    yield chunk.delta
                if chunk.is_final:
                    break
        except asyncio.CancelledError:
            logger.debug("InterviewAgent: generate_suggestion cancelled")
            raise
        except Exception:
            logger.exception("InterviewAgent: generate_suggestion failed")

    @property
    def suggestion_trigger(self) -> SuggestionTrigger | None:
        return self._suggestion_trigger

    # ── internals ─────────────────────────────────────────────────────────────

    async def _on_trigger_fired(self, request_id: int) -> None:
        """SuggestionTrigger 回调：在后台 task 内消费 generate_suggestion 流。"""
        if self._ws_sender is None:
            # WS sender 由上层在激活时注入；缺失时仅记录日志，不阻断
            logger.debug("InterviewAgent: trigger fired but no ws_sender attached")

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
            except asyncio.CancelledError:
                pass

        self._current_stream_task = asyncio.create_task(_runner())

    def attach_ws_sender(self, ws_sender) -> None:
        """由 Orchestrator 注入 WebSocket 推送回调（在 on_activate 之后）。"""
        self._ws_sender = ws_sender