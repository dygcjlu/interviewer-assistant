"""OpenAICompatibleClient 单元测试。

通过 mock openai.AsyncOpenAI 隔离外部依赖，不发起真实 API 调用。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from src.llm.client import OpenAICompatibleClient
from src.llm.config import LLMConfig
from src.llm.errors import LLMRateLimitError, LLMTimeoutError
from src.llm.protocol import ChatResponse, StreamChunk
from src.models.message import Message


def _make_config() -> LLMConfig:
    return LLMConfig(api_key="test-key", model="test-model", max_retries=2)


def _mock_chat_completion(content: str, prompt_tokens: int = 10, completion_tokens: int = 5) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


def _stream_chunk(delta: str = "", usage: SimpleNamespace | None = None) -> SimpleNamespace:
    if delta:
        choices = [SimpleNamespace(delta=SimpleNamespace(content=delta), finish_reason=None)]
    else:
        choices = []
    return SimpleNamespace(choices=choices, usage=usage)


class _AsyncStream:
    """模拟 openai AsyncStream 的最小异步迭代器。"""

    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> "_AsyncStream":
        return self

    async def __anext__(self) -> SimpleNamespace:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


@pytest.fixture
def client_with_mock():
    with patch("src.llm.client.openai.AsyncOpenAI") as async_openai_cls:
        instance = MagicMock()
        instance.chat = MagicMock()
        instance.chat.completions = MagicMock()
        instance.chat.completions.create = AsyncMock()
        async_openai_cls.return_value = instance
        client = OpenAICompatibleClient(_make_config())
        yield client, instance


@pytest.mark.asyncio
async def test_chat_returns_chat_response(client_with_mock):
    client, mock_instance = client_with_mock
    mock_instance.chat.completions.create.return_value = _mock_chat_completion(
        content="hello world", prompt_tokens=42, completion_tokens=7
    )

    result = await client.chat([Message(role="user", content="hi")])

    assert isinstance(result, ChatResponse)
    assert result.content == "hello world"
    assert result.prompt_tokens == 42
    assert result.completion_tokens == 7
    assert result.tool_calls is None

    create_kwargs = mock_instance.chat.completions.create.call_args.kwargs
    assert create_kwargs["model"] == "test-model"
    assert create_kwargs["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_chat_raises_llm_timeout_error(client_with_mock):
    client, mock_instance = client_with_mock
    mock_instance.chat.completions.create.side_effect = openai.APITimeoutError(
        request=MagicMock()
    )

    with pytest.raises(LLMTimeoutError):
        await client.chat([Message(role="user", content="hi")])

    assert mock_instance.chat.completions.create.call_count == 2


@pytest.mark.asyncio
async def test_chat_raises_llm_rate_limit_error(client_with_mock):
    client, mock_instance = client_with_mock
    rate_limit_exc = openai.RateLimitError(
        message="rate limit",
        response=MagicMock(status_code=429, headers={}),
        body=None,
    )
    mock_instance.chat.completions.create.side_effect = rate_limit_exc

    with pytest.raises(LLMRateLimitError):
        await client.chat([Message(role="user", content="hi")])


@pytest.mark.asyncio
async def test_chat_stream_yields_chunks(client_with_mock):
    client, mock_instance = client_with_mock
    usage = SimpleNamespace(prompt_tokens=15, completion_tokens=3)
    chunks = [
        _stream_chunk(delta="hel"),
        _stream_chunk(delta="lo"),
        _stream_chunk(delta="", usage=usage),
    ]
    mock_instance.chat.completions.create.return_value = _AsyncStream(chunks)

    collected: list[StreamChunk] = []
    async for chunk in client.chat_stream([Message(role="user", content="hi")]):
        collected.append(chunk)

    assert [c.delta for c in collected[:-1]] == ["hel", "lo"]
    final = collected[-1]
    assert final.is_final is True
    assert final.prompt_tokens == 15
    assert final.completion_tokens == 3
    assert final.delta == ""


def test_count_tokens_returns_positive_int(client_with_mock):
    client, _ = client_with_mock
    messages = [
        Message(role="system", content="You are a helpful interviewer."),
        Message(role="user", content="请介绍你的项目经验。"),
    ]
    result = client.count_tokens(messages)

    assert isinstance(result, int)
    assert result > 0


def test_count_tokens_handles_empty_content(client_with_mock):
    client, _ = client_with_mock
    result = client.count_tokens([Message(role="user", content=None)])
    assert result >= 1