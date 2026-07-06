"""Unit tests — LLM 客户端模块：OpenAICompatibleClient 静态方法 + count_tokens + 异常。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.protocol import ChatResponse, ToolFunction, ToolSchema
from src.models.exceptions import (
    LLMRateLimitError,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from src.models.message import FunctionCallInfo, Message, ToolCallInfo

# ── 辅助构造 ──────────────────────────────────────────────────────────────────


def _make_client():
    """构建 OpenAICompatibleClient（mock openai.AsyncOpenAI）。"""
    from src.llm.client import OpenAICompatibleClient
    from src.llm.config import LLMConfig

    config = LLMConfig(
        api_key="test-key", model="test-model", base_url="http://fake/v1"
    )
    with patch("src.llm.client.openai.AsyncOpenAI"):
        client = OpenAICompatibleClient(config)
    return client


# ── _message_to_dict ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMessageToDict:
    def test_basic_user_message(self):
        from src.llm.client import OpenAICompatibleClient

        m = Message(role="user", content="你好")
        d = OpenAICompatibleClient._message_to_dict(m)
        assert d["role"] == "user"
        assert d["content"] == "你好"
        assert "tool_calls" not in d

    def test_message_with_tool_calls(self):
        from src.llm.client import OpenAICompatibleClient

        tc = ToolCallInfo(
            id="call-001",
            type="function",
            function=FunctionCallInfo(name="my_tool", arguments='{"x": 1}'),
        )
        m = Message(role="assistant", content="", tool_calls=[tc])
        d = OpenAICompatibleClient._message_to_dict(m)
        assert "tool_calls" in d
        assert d["tool_calls"][0]["id"] == "call-001"
        assert d["tool_calls"][0]["function"]["name"] == "my_tool"

    def test_tool_result_message(self):
        from src.llm.client import OpenAICompatibleClient

        m = Message(role="tool", content='{"result": "ok"}', tool_call_id="call-001")
        d = OpenAICompatibleClient._message_to_dict(m)
        assert d["role"] == "tool"
        assert d["tool_call_id"] == "call-001"

    def test_none_content_not_included(self):
        from src.llm.client import OpenAICompatibleClient

        m = Message(role="assistant", content=None)
        d = OpenAICompatibleClient._message_to_dict(m)
        assert "content" not in d


# ── _tool_to_dict ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestToolToDict:
    def test_converts_tool_schema(self):
        from src.llm.client import OpenAICompatibleClient

        schema = ToolSchema(
            function=ToolFunction(
                name="my_func",
                description="做某事",
                parameters={"type": "object", "properties": {}},
            )
        )
        d = OpenAICompatibleClient._tool_to_dict(schema)
        assert d["type"] == "function"
        assert d["function"]["name"] == "my_func"
        assert d["function"]["description"] == "做某事"


# ── _build_chat_response ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestBuildChatResponse:
    def _make_raw_response(self, content="回复内容", tool_calls=None):
        msg = MagicMock()
        msg.content = content
        msg.tool_calls = tool_calls

        choice = MagicMock()
        choice.message = msg

        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5

        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = usage
        return resp

    def test_basic_response_parsed(self):
        from src.llm.client import OpenAICompatibleClient

        raw = self._make_raw_response("测试回复")
        result = OpenAICompatibleClient._build_chat_response(raw)
        assert isinstance(result, ChatResponse)
        assert result.content == "测试回复"
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5

    def test_empty_choices_raises(self):
        from src.llm.client import OpenAICompatibleClient

        raw = MagicMock()
        raw.choices = []
        with pytest.raises(LLMResponseError):
            OpenAICompatibleClient._build_chat_response(raw)

    def test_response_with_tool_calls(self):
        from src.llm.client import OpenAICompatibleClient

        fn = MagicMock()
        fn.name = "parse_resume_pdf"
        fn.arguments = '{"path": "/tmp/a.pdf"}'

        tc = MagicMock()
        tc.id = "tc-001"
        tc.type = "function"
        tc.function = fn

        raw = self._make_raw_response("", tool_calls=[tc])
        result = OpenAICompatibleClient._build_chat_response(raw)
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "tc-001"
        assert result.tool_calls[0].function.name == "parse_resume_pdf"

    def test_no_usage_defaults_to_zero(self):
        from src.llm.client import OpenAICompatibleClient

        raw = self._make_raw_response()
        raw.usage = None
        result = OpenAICompatibleClient._build_chat_response(raw)
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0


# ── count_tokens ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCountTokens:
    def test_count_tokens_returns_positive_int(self):
        client = _make_client()
        messages = [Message(role="user", content="这是一段中文测试消息")]
        count = client.count_tokens(messages)
        assert isinstance(count, int)
        assert count > 0

    def test_empty_messages_returns_positive(self):
        client = _make_client()
        count = client.count_tokens([])
        assert count >= 1  # max(result, 1)

    def test_longer_message_has_more_tokens(self):
        client = _make_client()
        short = [Message(role="user", content="短")]
        long = [Message(role="user", content="这是一段较长的中文消息，包含许多字符")]
        assert client.count_tokens(long) > client.count_tokens(short)

    def test_fallback_when_no_encoding(self):
        client = _make_client()
        client._encoding = None
        messages = [Message(role="user", content="测试")]
        count = client.count_tokens(messages)
        assert count >= 1


# ── chat 重试逻辑（mock openai 客户端）──────────────────────────────────────


@pytest.mark.unit
class TestChatRetry:
    @pytest.mark.asyncio
    async def test_chat_succeeds_on_first_attempt(self):
        client = _make_client()
        raw_resp = MagicMock()
        raw_resp.choices = [MagicMock()]
        raw_resp.choices[0].message.content = "ok"
        raw_resp.choices[0].message.tool_calls = None
        raw_resp.usage.prompt_tokens = 5
        raw_resp.usage.completion_tokens = 3
        client._client.chat.completions.create = AsyncMock(return_value=raw_resp)

        messages = [Message(role="user", content="hi")]
        result = await client.chat(messages)
        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_chat_raises_retry_exhausted_after_max_retries(self):
        import openai

        client = _make_client()
        client._config.max_retries = 2
        client._client.chat.completions.create = AsyncMock(
            side_effect=openai.APIConnectionError(request=MagicMock())
        )

        messages = [Message(role="user", content="hi")]
        with pytest.raises(LLMRetryExhaustedError):
            await client.chat(messages)

    @pytest.mark.asyncio
    async def test_chat_rate_limit_retries(self):
        import openai

        client = _make_client()
        client._config.max_retries = 2

        raw_resp = MagicMock()
        raw_resp.choices = [MagicMock()]
        raw_resp.choices[0].message.content = "ok"
        raw_resp.choices[0].message.tool_calls = None
        raw_resp.usage.prompt_tokens = 5
        raw_resp.usage.completion_tokens = 3

        # 第一次限流，第二次成功
        client._client.chat.completions.create = AsyncMock(
            side_effect=[
                openai.RateLimitError(
                    message="rate limit",
                    response=MagicMock(status_code=429),
                    body={},
                ),
                raw_resp,
            ]
        )
        messages = [Message(role="user", content="hi")]
        with patch("asyncio.sleep", AsyncMock()):
            result = await client.chat(messages)
        assert result.content == "ok"


# ── chat_stream ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestChatStream:
    @pytest.mark.asyncio
    async def test_chat_stream_yields_text_chunks(self):
        client = _make_client()

        # Build fake SSE chunks
        def _make_raw_chunk(text: str):
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = text
            chunk.choices[0].delta.tool_calls = None
            chunk.usage = None
            return chunk

        final_chunk = MagicMock()
        final_chunk.choices = []
        final_chunk.usage = MagicMock(prompt_tokens=5, completion_tokens=3)

        async def _fake_stream(*args, **kwargs):
            for c in [_make_raw_chunk("Hello"), _make_raw_chunk(" World"), final_chunk]:
                yield c

        client._client.chat.completions.create = AsyncMock(return_value=_fake_stream())

        from src.models.message import Message

        messages = [Message(role="user", content="say hi")]
        chunks = []
        async for chunk in client.chat_stream(messages):
            chunks.append(chunk)
        text_chunks = [c for c in chunks if not c.is_final]
        final = [c for c in chunks if c.is_final]
        assert any("Hello" in c.delta for c in text_chunks)
        assert len(final) == 1

    @pytest.mark.asyncio
    async def test_chat_stream_raises_on_rate_limit(self):
        import openai

        client = _make_client()

        async def _fail_stream(*args, **kwargs):
            raise openai.RateLimitError(
                message="rate limit", response=MagicMock(status_code=429), body={}
            )
            # generator must have at least one yield for Python
            yield  # pragma: no cover

        client._client.chat.completions.create = AsyncMock(return_value=_fail_stream())
        from src.models.message import Message

        messages = [Message(role="user", content="hi")]
        with pytest.raises(LLMRateLimitError):
            async for _ in client.chat_stream(messages):
                pass

    @pytest.mark.asyncio
    async def test_chat_stream_accumulates_tool_calls(self):
        client = _make_client()

        def _make_tc_chunk(idx: int, name: str = "", args: str = "", tc_id: str = ""):
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = None
            tc = MagicMock()
            tc.index = idx
            tc.id = tc_id
            tc.type = "function"
            tc.function = MagicMock()
            tc.function.name = name
            tc.function.arguments = args
            chunk.choices[0].delta.tool_calls = [tc]
            chunk.usage = None
            return chunk

        final_chunk = MagicMock()
        final_chunk.choices = []
        final_chunk.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

        async def _fake_stream(*args, **kwargs):
            for c in [
                _make_tc_chunk(0, name="my_tool", tc_id="tc-1"),
                _make_tc_chunk(0, args='{"key":'),
                _make_tc_chunk(0, args='"val"}'),
                final_chunk,
            ]:
                yield c

        client._client.chat.completions.create = AsyncMock(return_value=_fake_stream())

        from src.models.message import Message

        messages = [Message(role="user", content="use tool")]
        final = None
        async for chunk in client.chat_stream(messages):
            if chunk.is_final:
                final = chunk
        assert final is not None
        assert final.tool_calls is not None
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].function.name == "my_tool"
