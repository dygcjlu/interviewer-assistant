"""轻量 Metrics 单例 — 累积 LLM 调用 token / 耗时 / 错误计数。

S-11: 无第三方依赖；通过 GET /api/metrics 暴露 JSON，供 UI / 运维监控使用。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class Metrics:
    """进程级 Metrics 单例，线程/协程安全（Python GIL 保护 int += 操作）。"""

    _instance: ClassVar["Metrics | None"] = None

    requests_total: int = 0
    errors_total: int = 0
    tokens_prompt_total: int = 0
    tokens_completion_total: int = 0

    # 保留最近 200 次延迟样本（毫秒），用于计算 p50/p95
    _latency_samples: list[float] = field(default_factory=list)
    _MAX_SAMPLES: ClassVar[int] = 200

    _asr_latency_samples: list[float] = field(default_factory=list)
    suggestion_trigger_auto_count: int = 0
    suggestion_trigger_manual_count: int = 0

    _started_at: float = field(default_factory=time.time)

    @classmethod
    def get(cls) -> "Metrics":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例实例（仅用于测试隔离）

        警告：此方法仅供测试使用，生产代码不应调用。
        """
        cls._instance = None

    def record_asr_latency(self, elapsed_ms: float) -> None:
        if elapsed_ms > 0:
            self._asr_latency_samples.append(elapsed_ms)
            if len(self._asr_latency_samples) > self._MAX_SAMPLES:
                self._asr_latency_samples = self._asr_latency_samples[-self._MAX_SAMPLES:]

    def record_suggestion_trigger(self, mode: str) -> None:
        if mode == "auto":
            self.suggestion_trigger_auto_count += 1
        else:
            self.suggestion_trigger_manual_count += 1

    def record_request(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        elapsed_ms: float = 0.0,
        error: bool = False,
    ) -> None:
        self.requests_total += 1
        self.tokens_prompt_total += prompt_tokens
        self.tokens_completion_total += completion_tokens
        if error:
            self.errors_total += 1
        if elapsed_ms > 0:
            self._latency_samples.append(elapsed_ms)
            if len(self._latency_samples) > self._MAX_SAMPLES:
                self._latency_samples = self._latency_samples[-self._MAX_SAMPLES :]

    def _percentile(self, samples: list[float], p: float) -> float | None:
        if not samples:
            return None
        sorted_s = sorted(samples)
        idx = max(0, int(len(sorted_s) * p / 100) - 1)
        return round(sorted_s[idx], 1)

    def to_dict(self) -> dict:
        return {
            "requests_total": self.requests_total,
            "errors_total": self.errors_total,
            "tokens_prompt_total": self.tokens_prompt_total,
            "tokens_completion_total": self.tokens_completion_total,
            "tokens_total": self.tokens_prompt_total + self.tokens_completion_total,
            "latency_ms_p50": self._percentile(self._latency_samples, 50),
            "latency_ms_p95": self._percentile(self._latency_samples, 95),
            "asr_latency_p50_ms": self._percentile(self._asr_latency_samples, 50),
            "asr_latency_p99_ms": self._percentile(self._asr_latency_samples, 99),
            "suggestion_trigger_auto_count": self.suggestion_trigger_auto_count,
            "suggestion_trigger_manual_count": self.suggestion_trigger_manual_count,
            "uptime_sec": round(time.time() - self._started_at, 1),
        }
