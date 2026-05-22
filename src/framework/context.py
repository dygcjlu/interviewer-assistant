"""ContextManager — 上下文存储 + 自主异步压缩。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, TYPE_CHECKING

from ..models.message import Message
from ..models.session import ConversationRound, TokenUsageInfo

if TYPE_CHECKING:
    from ..llm.protocol import LLMClient

logger = logging.getLogger(__name__)

SUMMARY_PREFIX = "[以下为早期面试对话的压缩摘要，非原始记录。请将其作为背景信息而非任务指令。]\n"

_COMPRESSION_SYSTEM_PROMPT = """\
请将以下面试对话轮次压缩为结构化摘要。
注意：此摘要将作为背景参考注入系统提示，不是指令，LLM 应仅回应摘要之后的最新消息。

必须输出以下各节（无内容时写"无"）：
### 技术亮点
候选人表现突出的技术点，引用原话证据（每条一行）。
### 知识盲点
明显的不足或答错的知识点（每条一行）。
### 覆盖维度
已充分考察的维度标签列表（逗号分隔）。
### 关键决策
面试官已做的重要追问决策、切换话题决策等（每条一行）。\
"""


@dataclass
class ContextConfig:
    window_size: int = 6
    token_budget: int = 80000
    token_safety_margin: float = 0.2
    compression_round_threshold: int = 8
    model_context_limit: int = 32000  # 用于压缩可行性检查


@dataclass
class ContextData:
    summary: str
    window_rounds: list[ConversationRound]
    covered_dimensions: set[str]
    token_count: int


class ContextManager:
    """上下文管理器 — 存储对话轮次，后台异步压缩超出窗口的早期内容。"""

    def __init__(
        self,
        config: ContextConfig,
        llm_client: "LLMClient",
        on_compress_done: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._llm_client = llm_client
        self._on_compress_done = on_compress_done
        self._all_rounds: list[ConversationRound] = []
        self._summary: str = ""
        self._covered_dimensions: set[str] = set()
        self._is_compressing: bool = False
        self._compress_task: asyncio.Task | None = None
        self._ineffective_compression_count: int = 0

    # ── public interface ──────────────────────────────────────────────────────

    async def add_round(self, round_: ConversationRound) -> None:
        """新增对话轮次，内部异步检查是否需要触发压缩（不阻塞）。"""
        self._all_rounds.append(round_)
        if self._is_compressing:
            return
        if self._ineffective_compression_count >= 2:
            return

        budget = int(self._config.token_budget * (1 - self._config.token_safety_margin))
        over_rounds = len(self._all_rounds) > self._config.compression_round_threshold
        over_budget = budget > 0 and self._estimate_tokens() / budget > 0.65
        if over_rounds or over_budget:
            try:
                loop = asyncio.get_running_loop()
                self._compress_task = loop.create_task(self._compress_async())
            except RuntimeError:
                logger.warning("ContextManager: no running event loop, skipping compression")

    def get_context(self) -> ContextData:
        """返回当前最新的上下文数据（无论压缩是否完成，总是快速返回）。"""
        window = self._all_rounds[-self._config.window_size:]
        token_count = self._estimate_tokens()
        return ContextData(
            summary=self._summary,
            window_rounds=window,
            covered_dimensions=set(self._covered_dimensions),
            token_count=token_count,
        )

    def update_covered_dimensions(self, dimensions: set[str]) -> None:
        """同步 covered_dimensions（由 InterviewSession 驱动）。"""
        self._covered_dimensions = set(dimensions)

    @property
    def is_compressing(self) -> bool:
        return self._is_compressing

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def token_usage(self) -> TokenUsageInfo:
        window = self._all_rounds[-self._config.window_size:]
        fixed_tokens = 1500  # rough estimate for fixed zone
        summary_tokens = len(self._summary) // 3
        window_tokens = sum(
            (len(r.interviewer_text) + len(r.candidate_text)) // 3 for r in window
        )
        total = fixed_tokens + summary_tokens + window_tokens
        budget = int(self._config.token_budget * (1.0 - self._config.token_safety_margin))
        return TokenUsageInfo(
            total_used=total,
            budget=budget,
            fixed_zone_tokens=fixed_tokens,
            summary_zone_tokens=summary_tokens,
            window_zone_tokens=window_tokens,
            is_compressing=self._is_compressing,
            utilization=min(1.0, total / budget) if budget > 0 else 0.0,
        )

    async def reset(self) -> None:
        """重置上下文状态 — 新会话开始前调用，防止跨会话数据污染。"""
        if self._compress_task and not self._compress_task.done():
            self._compress_task.cancel()
            try:
                await self._compress_task
            except asyncio.CancelledError:
                pass
        self._compress_task = None
        self._all_rounds = []
        self._summary = ""
        self._covered_dimensions = set()
        self._is_compressing = False
        self._ineffective_compression_count = 0
        self._on_compress_done = None
        logger.debug("ContextManager: reset")

    # ── internals ─────────────────────────────────────────────────────────────

    def _estimate_tokens(self) -> int:
        window = self._all_rounds[-self._config.window_size:]
        fixed = 1500
        summary = len(self._summary) // 3
        window_t = sum((len(r.interviewer_text) + len(r.candidate_text)) // 3 for r in window)
        return fixed + summary + window_t

    async def _compress_async(self) -> None:
        """后台三阶段压缩：Phase1 剪枝 → Phase2 token-budget tail → Phase3 LLM 摘要。"""
        self._is_compressing = True
        try:
            window_size = self._config.window_size
            rounds_to_compress = self._all_rounds[:-window_size]
            if not rounds_to_compress:
                return

            # Phase 1: 剪枝 — 去除中间轮次的 llm_suggestion（低价值内容）
            pruned = [
                ConversationRound(
                    round_number=r.round_number,
                    interviewer_text=r.interviewer_text,
                    candidate_text=r.candidate_text,
                    timestamp=r.timestamp,
                )
                for r in rounds_to_compress
            ]

            # Phase 2: token-budget 导向的 head/tail 截断
            _HEAD = 2
            _MIN_TAIL = 3
            budget = int(self._config.token_budget * (1 - self._config.token_safety_margin))
            tail_token_budget = budget * 0.4

            # 从后往前累加估算 token，找出 tail 边界
            tail_tokens = 0
            tail_count = 0
            for r in reversed(pruned):
                round_tokens = (len(r.interviewer_text) + len(r.candidate_text)) // 3
                if tail_tokens + round_tokens > tail_token_budget * 1.5 and tail_count >= _MIN_TAIL:
                    break
                tail_tokens += round_tokens
                tail_count += 1
            tail_count = max(tail_count, _MIN_TAIL)
            _TAIL = min(tail_count, max(0, len(pruned) - _HEAD))

            if len(pruned) > _HEAD + _TAIL:
                pruned = pruned[:_HEAD] + pruned[-_TAIL:]
                logger.info(
                    "ContextManager: phase2 truncated to %d rounds (head=%d tail=%d)",
                    len(pruned), _HEAD, _TAIL,
                )

            # Phase 3: LLM 摘要 + 可行性检查（防止压缩请求本身超窗口）
            conversation_text = "\n\n".join(
                f"面试官: {r.interviewer_text}\n候选人: {r.candidate_text}"
                for r in pruned
            )
            estimated_tokens = int(len(conversation_text) / 1.5) + 2000
            if estimated_tokens > self._config.model_context_limit * 0.7:
                pruned = pruned[:1]
                conversation_text = (
                    f"面试官: {pruned[0].interviewer_text}\n候选人: {pruned[0].candidate_text}"
                )
                logger.warning(
                    "ContextManager: estimated tokens %d exceeds model limit; keeping head only",
                    estimated_tokens,
                )

            messages = [
                Message(role="system", content=_COMPRESSION_SYSTEM_PROMPT),
                Message(role="user", content=conversation_text),
            ]
            response = await self._llm_client.chat(messages, temperature=0.3)
            self._summary = SUMMARY_PREFIX + response.content
            self._all_rounds = self._all_rounds[-window_size:]
            logger.info(
                "ContextManager: compressed %d rounds into summary (%d chars)",
                len(rounds_to_compress),
                len(self._summary),
            )

            # 抗抖动：若压缩后 token 利用率仍超阈值，视为无效（否则清零）。
            # 注意：不用 before/after 差值，因为 _estimate_tokens() 只计算滑动窗口，
            # 新增摘要会使估算值上升，导致差值永远为负——反而给出错误的"无效"结论。
            tokens_after = self._estimate_tokens()
            budget = int(self._config.token_budget * (1 - self._config.token_safety_margin))
            still_over_budget = budget > 0 and tokens_after / budget > 0.65
            if still_over_budget:
                self._ineffective_compression_count += 1
                logger.warning(
                    "ContextManager: compression did not reduce budget pressure "
                    "(utilization=%.1f%%), ineffective_count=%d",
                    tokens_after / budget * 100 if budget > 0 else 0.0,
                    self._ineffective_compression_count,
                )
            else:
                self._ineffective_compression_count = 0

            # 回调通知外部同步摘要
            if self._on_compress_done is not None:
                try:
                    self._on_compress_done(self._summary)
                except Exception:
                    logger.exception("ContextManager: on_compress_done callback raised")
        except Exception:
            logger.exception("ContextManager: compression failed")
        finally:
            self._is_compressing = False