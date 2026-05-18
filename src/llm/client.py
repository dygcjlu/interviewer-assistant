"""OpenAI 兼容客户端实现。

通过 base_url 切换至国产模型（通义千问 / DeepSeek 等）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

from src.logging import bind_op

import openai
import tiktoken

from ..models.message import FunctionCallInfo, Message, ToolCallInfo
from ..models.exceptions import LLMRateLimitError, LLMTimeoutError
from .config import LLMConfig
from .protocol import ChatResponse, StreamChunk, ToolSchema

logger = logging.getLogger(__name__)

_PER_MESSAGE_OVERHEAD_TOKENS = 4
_TOKEN_SAFETY_MARGIN = 1.2


class OpenAICompatibleClient:
    """LLMClient Protocol 的默认实现。"""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        try:
            self._encoding: tiktoken.Encoding | None = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:
            logger.warning("tiktoken encoding load failed, fallback to char-based estimate: %s", exc)
            self._encoding = None

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.7,
        timeout_sec: float | None = None,
    ) -> ChatResponse:
        payload_messages = [self._message_to_dict(m) for m in messages]
        payload_tools = [self._tool_to_dict(t) for t in tools] if tools else None
        timeout = timeout_sec if timeout_sec is not None else self._config.timeout_sec

        bind_op("llm_chat")
        last_exc: Exception | None = None
        attempts = max(self._config.max_retries, 1)
        for attempt in range(attempts):
            start = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {
                    "model": self._config.model,
                    "messages": payload_messages,
                    "temperature": temperature,
                    "timeout": timeout,
                }
                if payload_tools:
                    kwargs["tools"] = payload_tools
                logger.info(
                    "LLM chat start model=%s messages=%d tools=%s attempt=%d/%d",
                    self._config.model,
                    len(payload_messages),
                    bool(payload_tools),
                    attempt + 1,
                    attempts,
                )
                response = await self._client.chat.completions.create(**kwargs)
                result = self._build_chat_response(response)
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.info(
                    "LLM chat done model=%s prompt_tokens=%d completion_tokens=%d elapsed_ms=%.1f",
                    self._config.model,
                    result.prompt_tokens,
                    result.completion_tokens,
                    elapsed_ms,
                )
                return result
            except openai.RateLimitError as exc:
                logger.warning(
                    "LLM rate limit hit (attempt %d/%d): %s", attempt + 1, attempts, exc
                )
                last_exc = LLMRateLimitError(str(exc))
            except openai.APITimeoutError as exc:
                logger.warning(
                    "LLM request timed out (attempt %d/%d): %s", attempt + 1, attempts, exc
                )
                last_exc = LLMTimeoutError(str(exc))

        assert last_exc is not None
        raise last_exc

    async def chat_stream(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        timeout_sec: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        payload_messages = [self._message_to_dict(m) for m in messages]
        timeout = timeout_sec if timeout_sec is not None else self._config.timeout_sec

        bind_op("llm_chat_stream")
        prompt_tokens = 0
        completion_tokens = 0
        start = time.perf_counter()
        logger.info("LLM chat_stream start model=%s messages=%d", self._config.model, len(payload_messages))
        try:
            stream = await self._client.chat.completions.create(
                model=self._config.model,
                messages=payload_messages,
                temperature=temperature,
                stream=True,
                stream_options={"include_usage": True},
                timeout=timeout,
            )
            async for raw_chunk in stream:
                usage = getattr(raw_chunk, "usage", None)
                if usage is not None:
                    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                if not raw_chunk.choices:
                    continue
                delta = raw_chunk.choices[0].delta.content or ""
                if delta:
                    yield StreamChunk(delta=delta)
        except openai.RateLimitError as exc:
            logger.warning("LLM stream rate limit hit: %s", exc)
            raise LLMRateLimitError(str(exc)) from exc
        except openai.APITimeoutError as exc:
            logger.warning("LLM stream timed out: %s", exc)
            raise LLMTimeoutError(str(exc)) from exc

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "LLM chat_stream done model=%s prompt_tokens=%d completion_tokens=%d elapsed_ms=%.1f",
            self._config.model,
            prompt_tokens,
            completion_tokens,
            elapsed_ms,
        )
        yield StreamChunk(
            delta="",
            is_final=True,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def count_tokens(self, messages: list[Message]) -> int:
        if self._encoding is not None:
            total = 0
            for m in messages:
                total += len(self._encoding.encode(m.content or ""))
                total += _PER_MESSAGE_OVERHEAD_TOKENS
        else:
            total = sum(len(m.content or "") // 3 for m in messages)

        result = int(total * _TOKEN_SAFETY_MARGIN)
        return max(result, 1)

    @staticmethod
    def _message_to_dict(m: Message) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": m.role}
        if m.content is not None:
            payload["content"] = m.content
        if m.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in m.tool_calls
            ]
        if m.tool_call_id is not None:
            payload["tool_call_id"] = m.tool_call_id
        return payload

    @staticmethod
    def _tool_to_dict(t: ToolSchema) -> dict[str, Any]:
        return {
            "type": t.type,
            "function": {
                "name": t.function.name,
                "description": t.function.description,
                "parameters": t.function.parameters,
            },
        }

    @staticmethod
    def _build_chat_response(response: Any) -> ChatResponse:
        choice = response.choices[0]
        message = choice.message
        content = message.content or ""

        tool_calls: list[ToolCallInfo] | None = None
        raw_tool_calls = getattr(message, "tool_calls", None)
        if raw_tool_calls:
            tool_calls = [
                ToolCallInfo(
                    id=tc.id,
                    type=getattr(tc, "type", "function"),
                    function=FunctionCallInfo(
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    ),
                )
                for tc in raw_tool_calls
            ]

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
        )