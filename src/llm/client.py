"""OpenAI 兼容客户端实现。

通过 base_url 切换至国产模型（通义千问 / DeepSeek 等）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import openai
import tiktoken

from src.logging import bind_op, truncate

from ..models.exceptions import (
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
    LLMRetryExhaustedError,
    LLMTimeoutError,
)
from ..models.message import FunctionCallInfo, Message, ToolCallInfo
from .config import LLMConfig
from .protocol import ChatResponse, StreamChunk, ToolSchema
from .providers import get_profile

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
            self._encoding: tiktoken.Encoding | None = tiktoken.get_encoding(
                "cl100k_base"
            )
        except Exception as exc:
            logger.warning(
                "tiktoken encoding load failed, fallback to char-based estimate: %s",
                exc,
            )
            self._encoding = None

    def _thinking_active(self) -> bool:
        """当前请求是否需要激活思考模式。"""
        profile = get_profile(self._config.provider)
        return self._config.enable_thinking and profile.supports_thinking

    def _include_reasoning_content(self) -> bool:
        """是否需要在消息体中回传 reasoning_content（DeepSeek 工具调用强制要求）。"""
        profile = get_profile(self._config.provider)
        return (
            self._config.enable_thinking
            and profile.supports_thinking
            and profile.thinking_requires_reasoning_content
        )

    def _build_kwargs(
        self,
        payload_messages: list[dict[str, Any]],
        payload_tools: list[dict[str, Any]] | None,
        temperature: float,
        timeout: float,
        stream: bool = False,
    ) -> dict[str, Any]:
        """根据 ProviderProfile 构建请求参数字典。"""
        profile = get_profile(self._config.provider)
        thinking_on = self._thinking_active()

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": payload_messages,
            "timeout": timeout,
        }

        # temperature：thinking 模式且平台禁止时不发
        if not (thinking_on and profile.thinking_disables_temperature):
            kwargs["temperature"] = temperature

        # 思考模式参数
        if thinking_on:
            kwargs["reasoning_effort"] = self._config.reasoning_effort
            if profile.thinking_extra_body:
                kwargs["extra_body"] = profile.thinking_extra_body

        if payload_tools:
            kwargs["tools"] = payload_tools

        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

        return kwargs

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.7,
        timeout_sec: float | None = None,
    ) -> ChatResponse:
        include_rc = self._include_reasoning_content()
        payload_messages = [self._message_to_dict(m, include_rc) for m in messages]
        payload_tools = [self._tool_to_dict(t) for t in tools] if tools else None
        timeout = timeout_sec if timeout_sec is not None else self._config.timeout_sec

        bind_op("llm_chat")
        last_exc: Exception | None = None
        attempts = max(self._config.max_retries, 1)
        for attempt in range(attempts):
            start = time.perf_counter()
            try:
                kwargs = self._build_kwargs(
                    payload_messages, payload_tools, temperature, timeout
                )
                messages_json = json.dumps(
                    payload_messages, ensure_ascii=False, default=str
                )
                logger.debug(
                    "llm_messages_full model=%s messages=%s",
                    self._config.model,
                    messages_json,
                )
                logger.info(
                    "llm_request model=%s messages=%d tools=%s attempt=%d/%d messages_body=%s",
                    self._config.model,
                    len(payload_messages),
                    bool(payload_tools),
                    attempt + 1,
                    attempts,
                    truncate(messages_json),
                )
                response = await self._client.chat.completions.create(**kwargs)
                result = self._build_chat_response(response)
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.info(
                    "llm_response model=%s prompt_tokens=%d completion_tokens=%d "
                    "elapsed_ms=%.1f content=%s",
                    self._config.model,
                    result.prompt_tokens,
                    result.completion_tokens,
                    elapsed_ms,
                    truncate(result.content),
                )
                return result
            except openai.RateLimitError as exc:
                logger.warning(
                    "LLM rate limit hit (attempt %d/%d): %s", attempt + 1, attempts, exc
                )
                last_exc = LLMRateLimitError(str(exc))
                # 限流：指数退避（2^attempt 秒），给服务端时间恢复
                if attempt < attempts - 1:
                    await asyncio.sleep(2**attempt)
            except openai.APITimeoutError as exc:
                logger.warning(
                    "LLM request timed out (attempt %d/%d): %s",
                    attempt + 1,
                    attempts,
                    exc,
                )
                last_exc = LLMTimeoutError(str(exc))
                if attempt < attempts - 1:
                    await asyncio.sleep(1.0)
            except openai.APIConnectionError as exc:
                logger.warning(
                    "LLM connection error (attempt %d/%d): %s",
                    attempt + 1,
                    attempts,
                    exc,
                )
                last_exc = LLMConnectionError(str(exc))
                if attempt < attempts - 1:
                    await asyncio.sleep(1.0)
        else:
            # M3-4: for-else — 循环耗尽所有重试次数仍未成功（每次都由 return 提前退出才算成功）
            raise LLMRetryExhaustedError(
                f"LLM 请求重试 {attempts} 次后仍失败，最后错误：{last_exc}"
            )

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.7,
        timeout_sec: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        include_rc = self._include_reasoning_content()
        payload_messages = [self._message_to_dict(m, include_rc) for m in messages]
        payload_tools = [self._tool_to_dict(t) for t in tools] if tools else None
        timeout = timeout_sec if timeout_sec is not None else self._config.timeout_sec

        bind_op("llm_chat_stream")
        prompt_tokens = 0
        completion_tokens = 0
        start = time.perf_counter()
        messages_json = json.dumps(payload_messages, ensure_ascii=False, default=str)
        logger.debug(
            "llm_messages_full model=%s messages=%s", self._config.model, messages_json
        )
        logger.info(
            "llm_stream_request model=%s messages=%d tools=%s messages_body=%s",
            self._config.model,
            len(payload_messages),
            bool(payload_tools),
            truncate(messages_json),
        )

        # Accumulate streaming tool_calls by index (OpenAI splits args across chunks)
        # key = index, value = {"id", "type", "name", "arguments"}
        _tc_acc: dict[int, dict[str, str]] = {}
        content_acc = ""

        try:
            kwargs = self._build_kwargs(
                payload_messages, payload_tools, temperature, timeout, stream=True
            )
            stream = await self._client.chat.completions.create(**kwargs)
            async for raw_chunk in stream:
                usage = getattr(raw_chunk, "usage", None)
                if usage is not None:
                    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                if not raw_chunk.choices:
                    continue
                delta_obj = raw_chunk.choices[0].delta

                # Text delta
                text = getattr(delta_obj, "content", None) or ""
                if text:
                    content_acc += text
                    yield StreamChunk(delta=text)

                # Tool call deltas — accumulate by index
                raw_tcs = getattr(delta_obj, "tool_calls", None)
                if raw_tcs:
                    for tc_delta in raw_tcs:
                        idx: int = tc_delta.index
                        if idx not in _tc_acc:
                            _tc_acc[idx] = {
                                "id": getattr(tc_delta, "id", "") or "",
                                "type": getattr(tc_delta, "type", "function")
                                or "function",
                                "name": "",
                                "arguments": "",
                            }
                        fn = getattr(tc_delta, "function", None)
                        if fn:
                            if getattr(fn, "name", None):
                                _tc_acc[idx]["name"] += fn.name
                            if getattr(fn, "arguments", None):
                                _tc_acc[idx]["arguments"] += fn.arguments

        except openai.RateLimitError as exc:
            logger.warning("LLM stream rate limit hit: %s", exc)
            raise LLMRateLimitError(str(exc)) from exc
        except openai.APITimeoutError as exc:
            logger.warning("LLM stream timed out: %s", exc)
            raise LLMTimeoutError(str(exc)) from exc
        except openai.APIConnectionError as exc:
            logger.warning("LLM stream connection error: %s", exc)
            raise LLMConnectionError(str(exc)) from exc

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "LLM chat_stream done model=%s tool_calls=%d prompt_tokens=%d "
            "completion_tokens=%d elapsed_ms=%.1f",
            self._config.model,
            len(_tc_acc),
            prompt_tokens,
            completion_tokens,
            elapsed_ms,
        )

        # Build accumulated tool_calls list if any (sorted by index)
        tool_calls: list[ToolCallInfo] | None = None
        if _tc_acc:
            tool_calls = [
                ToolCallInfo(
                    id=v["id"],
                    type=v["type"],
                    function=FunctionCallInfo(name=v["name"], arguments=v["arguments"]),
                )
                for _, v in sorted(_tc_acc.items())
            ]

        yield StreamChunk(
            delta="",
            is_final=True,
            tool_calls=tool_calls,
            accumulated_content=content_acc,
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
    def _message_to_dict(
        m: Message, include_reasoning_content: bool = False
    ) -> dict[str, Any]:
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
        # 仅当平台强制要求时才回传 reasoning_content（否则各平台可能报错或忽略）
        if m.reasoning_content is not None and include_reasoning_content:
            payload["reasoning_content"] = m.reasoning_content
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
        # M3-3: 兜底空 choices[]（通义千问等兼容端点偶发此情况）
        if not response.choices:
            raise LLMResponseError("LLM 返回了空 choices[]，无法解析响应")
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

        # 提取思考模式下的推理链内容（DeepSeek/Qwen3 思考版返回）
        reasoning_content: str | None = (
            getattr(message, "reasoning_content", None) or None
        )

        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            reasoning_content=reasoning_content,
        )
