"""InterviewAgent — 实时面试追问建议。"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import AsyncIterator, Callable, TYPE_CHECKING

from src.logging import bind_op, text_summary, truncate

from .base import AgentRequest, AgentResponse, BaseAgent
from ..audio.trigger import SuggestionTrigger
from ..framework.prompt_builder import AgentConfig, PromptBuilder
from ..framework.tool_registry import ToolRegistry
from ..models.message import Message
from ..models.session import InterviewSession
from ..storage.conversation_logger import ConversationLogger

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

        self._logger: ConversationLogger | None = None
        self._system_logged: bool = False
        self._current_round_getter: Callable[[], tuple[str, str]] | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def on_activate(self, session: InterviewSession) -> None:
        self._session = session
        self._system_logged = False
        self._logger = ConversationLogger(
            Path("conversations") / f"interview_agent_{session.id}.jsonl"
        )
        # 完整系统提示（含候选人信息）在首次 generate_suggestion 调用时写入日志
        # L4-8: 切到 manual 时 trigger 会调用 cancel_current_stream 取消已 fire 的 LLM 流
        self._suggestion_trigger = SuggestionTrigger(
            on_trigger=self._on_trigger_fired,
            silence_threshold_sec=self._silence_threshold,
            min_interval_sec=self._min_interval,
            on_cancel_current=self.cancel_current_stream,
        )
        logger.info("InterviewAgent activated for session %s", session.id)

    async def cancel_current_stream(self) -> None:
        """L4-8: 主动取消正在进行的 LLM 流式生成（切换到 manual 时调用）。"""
        task = self._current_stream_task
        if task is None or task.done():
            return
        logger.info("InterviewAgent: cancel_current_stream called")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("InterviewAgent: cancelled stream raised unexpected error")
        self._current_stream_task = None

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
        self._system_logged = False
        self._logger = None
        self.set_current_round_getter(None)
        logger.info("InterviewAgent deactivated")

    # ── public interface ──────────────────────────────────────────────────────

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        self._bind_log_context(request.type)
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

        self._bind_log_context("generate_suggestion")
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

        # 首次调用时将完整系统提示（含候选人信息）写入日志
        if not self._system_logged and messages and self._logger is not None:
            await self._logger.append_with_system(messages[0].content, [])
            self._system_logged = True

        pending_ivr, pending_cand = "", ""
        if self._current_round_getter is not None:
            pending_ivr, pending_cand = self._current_round_getter()

        if pending_ivr or pending_cand:
            current_text = (
                f"面试官：{pending_ivr}\n"
                f"候选人：{pending_cand}\n\n"
                "请结合以上所有对话记录、候选人简历和题目清单，给出一句追问建议或话题切换引导语，直接输出话术，无需解释。"
            )
        elif self._session.rounds:
            last_round = self._session.rounds[-1]
            current_text = (
                f"面试官：{last_round.interviewer_text}\n"
                f"候选人：{last_round.candidate_text}\n\n"
                "请结合以上所有对话记录、候选人简历和题目清单，给出一句追问建议或话题切换引导语，直接输出话术，无需解释。"
            )
        else:
            current_text = "面试还未开始，请根据题目清单给出第一个开场问题，直接输出话术。"

        user_msg = Message(role="user", content=current_text)
        if self._logger is not None:
            await self._logger.append([user_msg])
        messages.append(user_msg)

        # L4-7: token 预算硬保护——超限时截断历史中间段，仍超限则跳过本次建议
        messages = self._enforce_token_budget(messages)
        if messages is None:
            logger.warning(
                "InterviewAgent generate_suggestion skipped (token over budget) request_id=%d",
                request_id,
            )
            return

        try:
            response = await self.llm_client.chat(messages)
            reply_text = (response.content or "").strip()
            prompt_tokens = response.prompt_tokens
            completion_tokens = response.completion_tokens

            assistant_msg = Message(role="assistant", content=reply_text)
            if self._logger is not None:
                await self._logger.append([assistant_msg])

            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "suggestion_generated request_id=%d output_chars=%d "
                "prompt_tokens=%d completion_tokens=%d elapsed_ms=%.1f text=%s",
                request_id,
                len(reply_text),
                prompt_tokens,
                completion_tokens,
                elapsed_ms,
                truncate(reply_text),
            )

            if reply_text:
                yield reply_text

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

    def _enforce_token_budget(self, messages: list[Message]) -> list[Message] | None:
        """L4-7: 主动校验 prompt token，超限时截断历史中间段，仍超则返回 None 跳过。

        策略：
        - budget = context_manager 配置的 token_budget × (1 - safety_margin)
        - 超出时保留 system（第 0 条）+ 最近 K 轮对话 + 最后一条 user（最终问题）
        - K 从大到小递减直至满足预算，最少保留 2 条对话；K=0 仍超限说明 fixed zone 单独过大，跳过
        """
        try:
            cfg = self.context_manager._config  # 内部访问，但本模块同包
            limit = int(cfg.token_budget * (1.0 - cfg.token_safety_margin))
        except Exception:
            limit = 64_000  # 兜底

        total = self.llm_client.count_tokens(messages)
        if total <= limit:
            return messages
        if len(messages) <= 3:
            logger.warning(
                "InterviewAgent token over budget but messages too short to trim: %d > %d",
                total, limit,
            )
            return None

        # 保留 system（0）+ 最后一条 user（最终问题）+ 最近 K 轮
        system = messages[0]
        last_user = messages[-1]
        middle = messages[1:-1]
        for keep in (8, 6, 4, 2):
            trimmed = [system] + middle[-keep:] + [last_user]
            tokens = self.llm_client.count_tokens(trimmed)
            if tokens <= limit:
                logger.warning(
                    "InterviewAgent token over budget, trimmed history middle=%d->%d (keep_last=%d) tokens=%d/%d",
                    len(middle), keep, keep, tokens, limit,
                )
                return trimmed
        logger.warning(
            "InterviewAgent token still over budget after max trimming: %d > %d (fixed zone too large)",
            self.llm_client.count_tokens([system, last_user]), limit,
        )
        return None

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
            tokens_yielded = 0
            accumulated_text = ""
            try:
                async for token in self.generate_suggestion(request_id):
                    tokens_yielded += 1
                    accumulated_text += token
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
                                "text": accumulated_text,
                                "skipped": tokens_yielded == 0,
                            }
                        )
                    except Exception:
                        logger.exception("InterviewAgent: ws_sender final failed")
                logger.info(
                    "InterviewAgent suggestion_stream finished request_id=%d skipped=%s",
                    request_id,
                    tokens_yielded == 0,
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

    def set_current_round_getter(
        self, getter: Callable[[], tuple[str, str]] | None
    ) -> None:
        """注入或清空当前轮次文本 getter，由 InterviewController 在 audio.start() 后调用。"""
        self._current_round_getter = getter