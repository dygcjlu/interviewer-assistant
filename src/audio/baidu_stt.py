"""BaiduRealtimeSTT stub — 测试环境使用 Mock 实现替代真实百度实时 STT。

真实实现依赖百度 ASR WebSocket API，MVP 测试阶段暂不实现。
"""
from __future__ import annotations

from .mock import MockSTTEngine as _MockSTTEngine


class BaiduRealtimeSTT(_MockSTTEngine):
    """Stub wrapper that accepts the channel keyword argument passed by main.py."""

    def __init__(self, channel: str = "candidate") -> None:
        super().__init__()

__all__ = ["BaiduRealtimeSTT"]
