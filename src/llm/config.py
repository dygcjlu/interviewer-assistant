"""LLM 客户端配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LLMConfig:
    api_key: str
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-plus"
    timeout_sec: float = 60.0
    max_retries: int = 2
    # 提供商标识 — 对应 src/llm/providers.py PROFILES 注册表键值
    # 已知值：openai_compat | qwen | deepseek | qwen_thinking
    provider: str = "qwen"
    # 是否激活思考模式（requires provider.supports_thinking=True 才生效）
    enable_thinking: bool = False
    reasoning_effort: str = "high"  # "high" | "max"
