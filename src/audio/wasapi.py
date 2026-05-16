"""WasapiCapturer stub — 测试环境使用 Mock 实现替代真实 WASAPI 采集。

真实 WASAPI 实现依赖 soundcard 库和 Windows 音频设备，MVP 测试阶段暂不实现。
"""
from __future__ import annotations

from .mock import MockAudioCapturer as WasapiCapturer

__all__ = ["WasapiCapturer"]
