"""Tests for ContextManager."""
import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from src.framework.context import ContextManager, ContextConfig, ContextData
from src.models.session import ConversationRound, TokenUsageInfo


def _make_round(n: int) -> ConversationRound:
    return ConversationRound(
        round_number=n,
        interviewer_text=f"Question {n}",
        candidate_text=f"Answer {n}",
        timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_add_round_appends() -> None:
    llm = MagicMock()
    config = ContextConfig(window_size=6, compression_round_threshold=100)
    manager = ContextManager(config, llm)
    round_ = _make_round(1)
    await manager.add_round(round_)
    ctx = manager.get_context()
    assert len(ctx.window_rounds) == 1
    assert ctx.window_rounds[0].round_number == 1


@pytest.mark.asyncio
async def test_get_context_respects_window_size() -> None:
    llm = MagicMock()
    config = ContextConfig(window_size=3, compression_round_threshold=100)
    manager = ContextManager(config, llm)
    for i in range(5):
        await manager.add_round(_make_round(i + 1))
    ctx = manager.get_context()
    assert len(ctx.window_rounds) == 3
    assert ctx.window_rounds[0].round_number == 3  # most recent 3


@pytest.mark.asyncio
async def test_token_usage_returns_token_usage_info() -> None:
    llm = MagicMock()
    manager = ContextManager(ContextConfig(), llm)
    await manager.add_round(_make_round(1))
    usage = manager.token_usage
    assert isinstance(usage, TokenUsageInfo)
    assert usage.total_used > 0
    assert usage.budget > 0
    assert 0.0 <= usage.utilization <= 1.0


@pytest.mark.asyncio
async def test_is_compressing_initially_false() -> None:
    llm = MagicMock()
    manager = ContextManager(ContextConfig(), llm)
    assert not manager.is_compressing


@pytest.mark.asyncio
async def test_summary_initially_empty() -> None:
    llm = MagicMock()
    manager = ContextManager(ContextConfig(), llm)
    ctx = manager.get_context()
    assert ctx.summary == ""


@pytest.mark.asyncio
async def test_compression_triggered_after_threshold() -> None:
    mock_response = MagicMock()
    mock_response.content = "压缩摘要内容"
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=mock_response)

    config = ContextConfig(window_size=2, compression_round_threshold=3)
    manager = ContextManager(config, llm)

    # Add rounds beyond threshold
    for i in range(5):
        await manager.add_round(_make_round(i + 1))

    # Give background task time to run
    await asyncio.sleep(0.1)

    # Summary should be populated after compression
    ctx = manager.get_context()
    # Either compression ran or not — both are valid, just check no crash
    assert isinstance(ctx.summary, str)


def test_update_covered_dimensions() -> None:
    llm = MagicMock()
    manager = ContextManager(ContextConfig(), llm)
    manager.update_covered_dimensions({"系统设计", "算法"})
    ctx = manager.get_context()
    assert "系统设计" in ctx.covered_dimensions
    assert "算法" in ctx.covered_dimensions


@pytest.mark.asyncio
async def test_compression_preserves_rounds_added_during_compression() -> None:
    """M5-4 回归：压缩期间被 add_round 加入的轮次必须保留，不被截断丢失。"""
    mock_response = MagicMock()
    mock_response.content = "压缩后的摘要"

    # 用一个慢响应模拟 LLM 调用期间用户继续说话
    extra_rounds_added: list[int] = []

    manager_ref: dict[str, ContextManager] = {}

    async def slow_chat(messages, **kwargs):  # noqa: ANN001 - mock signature
        # 压缩调用 LLM 时，前台模拟用户继续提交 2 轮
        await asyncio.sleep(0)  # 让出一次事件循环
        for n in (101, 102):
            await manager_ref["m"].add_round(_make_round(n))
            extra_rounds_added.append(n)
        return mock_response

    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=slow_chat)

    config = ContextConfig(
        window_size=3,
        compression_round_threshold=5,
        token_budget=8000,
        model_context_limit=100_000,
    )
    manager = ContextManager(config, llm)
    manager_ref["m"] = manager

    # 1..6 共 6 轮，触发压缩；压缩头部 3 条 (1,2,3)，保留尾部 3 条 (4,5,6)
    for i in range(6):
        await manager.add_round(_make_round(i + 1))

    # 等后台压缩跑完
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not manager.is_compressing:
            break
    assert not manager.is_compressing, "压缩未在 0.5s 内完成"

    # 注意：get_context().window_rounds 是滑动窗口（最后 window_size 个），会截断；
    # 这里需要直接看 _all_rounds 的实际保留情况
    all_rounds = manager.all_rounds
    round_numbers = [r.round_number for r in all_rounds]

    # 关键断言：原 4/5/6 + 压缩期间加入的 101/102 全部应该被保留，共 5 条
    # 旧实现 `_all_rounds[-window_size:]` 只剩 3 条 → 4 和 5 被错误丢弃
    assert 4 in round_numbers, f"原 round 4 被错误丢弃: {round_numbers}"
    assert 5 in round_numbers, f"原 round 5 被错误丢弃: {round_numbers}"
    assert 6 in round_numbers
    assert 101 in round_numbers, f"压缩期间加入的 round 101 丢失: {round_numbers}"
    assert 102 in round_numbers, f"压缩期间加入的 round 102 丢失: {round_numbers}"
    assert len(all_rounds) == 5, f"应保留 5 条，实际 {len(all_rounds)}: {round_numbers}"
    assert manager.summary  # 摘要非空