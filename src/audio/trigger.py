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
    ) -> None:
        self._on_trigger = on_trigger
        self._silence_threshold = silence_threshold_sec
        self._min_interval = min_interval_sec
        self._mode: str = "auto"
        self._request_id: int = 0
        self._last_trigger_time: float = 0.0
        self._pending_task: asyncio.Task | None = None

    # ── public interface ──────────────────────────────────────────────────────

    def on_candidate_segment(self, segment: TranscriptSegment) -> None:
        """接收候选人 is_final segment，重置沉默定时器（仅 auto 模式）。"""
        if self._mode != "auto" or not segment.is_final:
            return
        self.cancel_pending()
        try:
            loop = asyncio.get_running_loop()
            self._pending_task = loop.create_task(self._silence_timer())
        except RuntimeError:
            logger.warning("SuggestionTrigger: no running event loop, cannot schedule timer")

    def set_mode(self, mode: str) -> None:
        """切换触发模式 'auto' | 'manual'。切换到 manual 时取消待触发定时器。"""
        if mode not in ("auto", "manual"):
            raise ValueError(f"Invalid trigger mode: {mode!r}")
        self._mode = mode
        if mode == "manual":
            self.cancel_pending()

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
        try:
            await self._on_trigger(req_id)
        except Exception:
            logger.exception("SuggestionTrigger: on_trigger callback raised an exception")