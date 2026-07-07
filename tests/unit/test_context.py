"""Unit tests — ContextManager token 精确计数（count_tokens 单次调用）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.framework.context import ContextConfig, ContextManager
from src.models.message import Message
from src.models.session import ConversationRound


class _FakeLLM:
    """count_tokens 返回可预测的整数：每条消息 = 内容字符数 + 固定 overhead。"""

    def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") + 4 for m in messages)

    async def chat(self, *a, **k):
        raise AssertionError("not used in this test")


@pytest.mark.unit
def test_estimate_tokens_uses_count_tokens_single_call():
    """_estimate_tokens() 必须对整份虚拟消息列表整体调用一次 count_tokens，
    而不是逐段构造 Message 再分别调用后在 Python 里手动求和。

    _FakeLLM 原本的实现是逐消息线性可加的，无法区分"单次整体调用"与"多次
    调用后求和"这两种模式（数学结果恰好相同），因此改用 MagicMock 直接断言
    调用次数，并检查单次调用实际传入的消息列表内容，才能真正捕获回归。
    """
    llm = _FakeLLM()
    llm.count_tokens = MagicMock(return_value=42)
    cm = ContextManager(ContextConfig(), llm)
    cm._summary = "摘要内容"
    cm._all_rounds = [
        ConversationRound(round_number=1, interviewer_text="问题一", candidate_text="回答一"),
        ConversationRound(round_number=2, interviewer_text="问题二", candidate_text="回答二"),
    ]

    tokens = cm._estimate_tokens()

    assert tokens == 42
    llm.count_tokens.assert_called_once()
    # fixed(system 提示，1 条) + summary(1 条) + 每轮 1 条(2 条) = 4 条虚拟消息，
    # 且必须是同一次调用传入的单一 list，而非多次调用后拼接/累加。
    virtual_messages = llm.count_tokens.call_args[0][0]
    assert isinstance(virtual_messages, list)
    assert len(virtual_messages) == 4
    assert all(isinstance(m, Message) for m in virtual_messages)
