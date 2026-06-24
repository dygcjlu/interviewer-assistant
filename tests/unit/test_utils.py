"""Unit tests — utils 模块：safe_float、write_atomic、Metrics。"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.utils.numeric import safe_float
from src.utils.atomic_io import write_atomic
from src.utils.metrics import Metrics


# ── safe_float ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSafeFloat:
    def test_none_returns_default(self):
        assert safe_float(None) == 0.0

    def test_none_custom_default(self):
        assert safe_float(None, default=3.14) == 3.14

    def test_int_converts(self):
        assert safe_float(5) == 5.0

    def test_float_passthrough(self):
        assert safe_float(3.14) == pytest.approx(3.14)

    def test_valid_string_converts(self):
        assert safe_float("7.5") == pytest.approx(7.5)

    def test_string_with_whitespace(self):
        assert safe_float("  2.0  ") == pytest.approx(2.0)

    def test_invalid_string_returns_default(self):
        assert safe_float("N/A") == 0.0

    def test_empty_string_returns_default(self):
        assert safe_float("") == 0.0

    def test_list_returns_default(self):
        assert safe_float([1, 2]) == 0.0

    def test_zero_converts(self):
        assert safe_float(0) == 0.0

    def test_negative_converts(self):
        assert safe_float(-3.5) == pytest.approx(-3.5)


# ── write_atomic ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestWriteAtomic:
    def test_creates_file_with_content(self, tmp_path):
        target = tmp_path / "output.txt"
        write_atomic(target, "hello world")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_overwrites_existing_file(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("old content")
        write_atomic(target, "new content")
        assert target.read_text(encoding="utf-8") == "new content"

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "file.txt"
        write_atomic(target, "data")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "data"

    def test_no_tmp_files_remain_after_success(self, tmp_path):
        target = tmp_path / "clean.txt"
        write_atomic(target, "content")
        tmp_files = list(tmp_path.glob(".tmp_*.tmp"))
        assert tmp_files == []

    def test_unicode_content_written_correctly(self, tmp_path):
        target = tmp_path / "unicode.txt"
        write_atomic(target, "中文内容 🎉")
        assert target.read_text(encoding="utf-8") == "中文内容 🎉"

    def test_empty_content(self, tmp_path):
        target = tmp_path / "empty.txt"
        write_atomic(target, "")
        assert target.read_text(encoding="utf-8") == ""


# ── Metrics ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMetrics:
    def setup_method(self):
        # 每次测试重置单例
        Metrics._instance = None

    def test_get_returns_same_instance(self):
        m1 = Metrics.get()
        m2 = Metrics.get()
        assert m1 is m2

    def test_initial_counters_are_zero(self):
        m = Metrics.get()
        assert m.requests_total == 0
        assert m.errors_total == 0
        assert m.tokens_prompt_total == 0
        assert m.tokens_completion_total == 0

    def test_record_request_increments_total(self):
        m = Metrics.get()
        m.record_request(prompt_tokens=10, completion_tokens=5)
        assert m.requests_total == 1
        assert m.tokens_prompt_total == 10
        assert m.tokens_completion_total == 5

    def test_record_error_increments_error_count(self):
        m = Metrics.get()
        m.record_request(error=True)
        assert m.errors_total == 1

    def test_record_latency_sample_stored(self):
        m = Metrics.get()
        m.record_request(elapsed_ms=150.0)
        assert len(m._latency_samples) == 1
        assert m._latency_samples[0] == 150.0

    def test_latency_samples_capped_at_max(self):
        m = Metrics.get()
        for i in range(250):
            m.record_request(elapsed_ms=float(i))
        assert len(m._latency_samples) <= Metrics._MAX_SAMPLES

    def test_to_dict_contains_required_keys(self):
        m = Metrics.get()
        d = m.to_dict()
        for key in [
            "requests_total", "errors_total", "tokens_prompt_total",
            "tokens_completion_total", "tokens_total", "latency_ms_p50",
            "latency_ms_p95", "uptime_sec",
        ]:
            assert key in d

    def test_to_dict_tokens_total_sum(self):
        m = Metrics.get()
        m.record_request(prompt_tokens=20, completion_tokens=10)
        d = m.to_dict()
        assert d["tokens_total"] == 30

    def test_latency_p50_none_when_no_samples(self):
        m = Metrics.get()
        d = m.to_dict()
        assert d["latency_ms_p50"] is None

    def test_latency_p50_and_p95_computed(self):
        m = Metrics.get()
        for v in [100.0, 200.0, 300.0, 400.0, 500.0]:
            m.record_request(elapsed_ms=v)
        d = m.to_dict()
        assert d["latency_ms_p50"] is not None
        assert d["latency_ms_p95"] is not None

    def test_uptime_sec_positive(self):
        m = Metrics.get()
        time.sleep(0.01)
        d = m.to_dict()
        assert d["uptime_sec"] >= 0.0

    def test_record_asr_latency_stored(self):
        m = Metrics.get()
        m.record_asr_latency(120.0)
        m.record_asr_latency(80.0)
        assert len(m._asr_latency_samples) == 2

    def test_asr_latency_p50_p99_in_to_dict(self):
        m = Metrics.get()
        for v in [100.0, 200.0, 300.0]:
            m.record_asr_latency(v)
        d = m.to_dict()
        assert d["asr_latency_p50_ms"] is not None
        assert d["asr_latency_p99_ms"] is not None

    def test_asr_latency_none_when_no_samples(self):
        m = Metrics.get()
        d = m.to_dict()
        assert d["asr_latency_p50_ms"] is None
        assert d["asr_latency_p99_ms"] is None

    def test_record_suggestion_trigger_auto(self):
        m = Metrics.get()
        m.record_suggestion_trigger("auto")
        m.record_suggestion_trigger("auto")
        assert m.suggestion_trigger_auto_count == 2
        assert m.suggestion_trigger_manual_count == 0

    def test_record_suggestion_trigger_manual(self):
        m = Metrics.get()
        m.record_suggestion_trigger("manual")
        assert m.suggestion_trigger_manual_count == 1
        assert m.suggestion_trigger_auto_count == 0

    def test_suggestion_trigger_counts_in_to_dict(self):
        m = Metrics.get()
        m.record_suggestion_trigger("auto")
        m.record_suggestion_trigger("manual")
        d = m.to_dict()
        assert d["suggestion_trigger_auto_count"] == 1
        assert d["suggestion_trigger_manual_count"] == 1

    def test_to_dict_contains_all_new_keys(self):
        m = Metrics.get()
        d = m.to_dict()
        for key in ["asr_latency_p50_ms", "asr_latency_p99_ms",
                    "suggestion_trigger_auto_count", "suggestion_trigger_manual_count"]:
            assert key in d
