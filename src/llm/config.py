"""LLM 客户端配置。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LLMConfig:
    api_key: str
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-plus"
    timeout_sec: float = 30.0
    max_retries: int = 2