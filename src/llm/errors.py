"""LLM 异常 — 从 models.exceptions 统一导出，消除重复定义。"""
from __future__ import annotations

from ..models.exceptions import (
    InterviewAssistantError as LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMRetryExhaustedError,
    LLMTimeoutError,
)

__all__ = [
    "LLMError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMResponseError",
    "LLMRetryExhaustedError",
]