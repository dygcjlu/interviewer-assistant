"""建议生成触发器 — 沉默计时 + 防抖 + 最小间隔。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from .protocol import TranscriptSegment

logger = logging.getLogger(__name__)


class SuggestionTrigger:
    """纯触发决策组件，满足条件时通过回调通知 InterviewAgent.generate_suggestion()。"""

    def __init__(
        self,
        on_trigger: Callable[[int], Awaitable[None]],
        silence_threshold_sec: float = 2.0,
        min_interval_sec: float = 5.0,
        on_cancel_current: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._on_trigger = on_trigger
        # L4-8: 切到 manual 时调用此回调取消"已 fire 但仍在生成中"的 LLM 流。
        self._on_cancel_current = on_cancel_current
        self._silence_threshold = silence_threshold_sec
        self._min_interval = min_interval_sec
        self._mode: str = "auto"
        self._request_id: int = 0
        self._last_trigger_time: float = 0.0
        self._pending_task: asyncio.Task | None = None

    # ── public interface ──────────────────────────────────────────────────────

    def on_candidate_segment(self, segment: TranscriptSegment) -> None:
        """接收候选人 segment，管理沉默计时器（仅 auto 模式）。

        is_final=False：候选人正在说话，取消待触发计时器。
        is_final=True ：候选人本句结束，重新开始沉默倒计时。
        """
        if self._mode != "auto":
            return
        if not segment.is_final:
            self.cancel_pending()
            return
        self.cancel_pending()
        try:
            loop = asyncio.get_running_loop()
            self._pending_task = loop.create_task(self._silence_timer())
        except RuntimeError:
            logger.warning("SuggestionTrigger: no running event loop, cannot schedule timer")

    def set_mode(self, mode: str) -> None:
        """切换触发模式 'auto' | 'manual'。

        L4-8: 切到 manual 不仅取消待触发定时器，还取消"已 fire 但仍在 LLM 流式生成中"的请求，
              避免用户切换后仍看到一段刚生成的建议出现。
        """
        if mode not in ("auto", "manual"):
            raise ValueError(f"Invalid trigger mode: {mode!r}")
        prev = self._mode
        self._mode = mode
        if mode == "manual":
            self.cancel_pending()
            if prev == "auto" and self._on_cancel_current is not None:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._on_cancel_current())
                except RuntimeError:
                    logger.warning(
                        "SuggestionTrigger: no running event loop, cannot cancel current stream"
                    )

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def next_request_id(self) -> int:
        return self._request_id

    def cancel_pending(self) -> None:
        """取消待触发的定时器。"""
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
        self._pending_task = None

    def stop(self) -> None:
        """停止所有定时器，释放资源。"""
        self.cancel_pending()

    # ── internals ─────────────────────────────────────────────────────────────

    async def _silence_timer(self) -> None:
        await asyncio.sleep(self._silence_threshold)
        now = time.monotonic()
        if now - self._last_trigger_time < self._min_interval:
            logger.debug("SuggestionTrigger: skipping trigger — min_interval not elapsed")
            return
        req_id = self._request_id
        self._request_id += 1
        self._last_trigger_time = now
        logger.info("SuggestionTrigger: firing request_id=%d", req_id)
        from ..utils.metrics import Metrics
        Metrics.get().record_suggestion_trigger("auto")
        try:
            await self._on_trigger(req_id)
        except Exception:
            logger.exception("SuggestionTrigger: on_trigger callback raised an exception")