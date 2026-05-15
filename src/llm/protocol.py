"""LLM 客户端 Protocol 与返回类型定义。

所有 Agent 通过此接口与 LLM 交互；实现在 src/llm/client.py。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from ..models.message import Message, ToolCallInfo


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[ToolCallInfo] | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class StreamChunk:
    delta: str
    is_final: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class ToolFunction:
    name: str
    description: str
    parameters: dict                       # JSON Schema


@dataclass
class ToolSchema:
    function: ToolFunction
    type: str = "function"


class LLMClient(Protocol):
    """LLM 客户端抽象接口 — OpenAI SDK 兼容模式。"""

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.7,
        timeout_sec: float | None = None,
    ) -> ChatResponse:
        """同步请求（等待完整响应）。超时抛出 LLMTimeoutError，限流抛出 LLMRateLimitError。"""
        ...

    async def chat_stream(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        timeout_sec: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式请求（逐 token 返回），用于实时追问建议推送到前端。"""
        ...

    def count_tokens(self, messages: list[Message]) -> int:
        """基于 tiktoken 预估 token 数（发送前粗估）。对国产模型中文分词有偏差，预留 20% 余量。"""
        ...
