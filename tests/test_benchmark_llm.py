"""Unit tests for scripts/benchmark_llm.py pure functions."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.benchmark_llm import (
    MODEL_CONFIGS,
    NO_THINK,
    BenchmarkResult,
    ModelConfig,
    ThinkingConfig,
    build_request_kwargs,
    filter_configs,
)

_DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEEPSEEK_URL = "https://api.deepseek.com"
_BAILIAN_THINK = ThinkingConfig(
    extra_body={"enable_thinking": True},
    suppress_temperature=False,
    reasoning_effort=None,
)
_DS_OFFICIAL_THINK = ThinkingConfig(
    extra_body={"thinking": {"type": "enabled"}},
    suppress_temperature=True,
    reasoning_effort="high",
)


def _qwen_config(label: str, model: str) -> ModelConfig:
    return ModelConfig(
        label=label,
        model=model,
        base_url=_DASHSCOPE_URL,
        api_key_env="LLM_API_KEY",
        no_think=NO_THINK,
        with_think=_BAILIAN_THINK,
    )


class TestBuildRequestKwargs:
    def test_no_think_includes_temperature(self) -> None:
        cfg = _qwen_config("qwen-plus", "qwen3.7-plus")
        kwargs = build_request_kwargs(cfg, cfg.no_think, temperature=0.1, timeout=30.0)
        assert kwargs["temperature"] == 0.1
        assert "extra_body" not in kwargs
        assert kwargs["stream"] is True

    def test_no_think_has_no_reasoning_effort(self) -> None:
        cfg = _qwen_config("qwen-plus", "qwen3.7-plus")
        kwargs = build_request_kwargs(cfg, cfg.no_think, temperature=0.1, timeout=30.0)
        assert "reasoning_effort" not in kwargs

    def test_bailian_thinking_adds_extra_body(self) -> None:
        cfg = _qwen_config("qwen-plus", "qwen3.7-plus")
        kwargs = build_request_kwargs(
            cfg, cfg.with_think, temperature=0.1, timeout=30.0
        )
        assert kwargs["extra_body"] == {"enable_thinking": True}
        # Qwen 思考模式仍允许传 temperature
        assert kwargs["temperature"] == 0.1

    def test_deepseek_official_suppresses_temperature(self) -> None:
        cfg = ModelConfig(
            label="ds-official",
            model="deepseek-v4-pro",
            base_url=_DEEPSEEK_URL,
            api_key_env="DEEPSEEK_API_KEY",
            no_think=NO_THINK,
            with_think=_DS_OFFICIAL_THINK,
        )
        kwargs = build_request_kwargs(
            cfg, _DS_OFFICIAL_THINK, temperature=0.1, timeout=30.0
        )
        assert "temperature" not in kwargs
        assert kwargs["reasoning_effort"] == "high"
        assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}

    def test_stream_options_always_present(self) -> None:
        cfg = _qwen_config("qwen-plus", "qwen3.7-plus")
        kwargs = build_request_kwargs(cfg, cfg.no_think, temperature=0.1, timeout=30.0)
        assert kwargs["stream_options"] == {"include_usage": True}

    def test_timeout_in_kwargs(self) -> None:
        cfg = _qwen_config("qwen-plus", "qwen3.7-plus")
        kwargs = build_request_kwargs(cfg, cfg.no_think, temperature=0.1, timeout=45.0)
        assert kwargs["timeout"] == 45.0

    def test_model_in_kwargs(self) -> None:
        cfg = _qwen_config("qwen-plus", "qwen3.7-plus")
        kwargs = build_request_kwargs(cfg, cfg.no_think, temperature=0.1, timeout=30.0)
        assert kwargs["model"] == "qwen3.7-plus"


class TestFilterConfigs:
    def test_filter_by_keyword_qwen(self) -> None:
        filtered = filter_configs(MODEL_CONFIGS, filter_str="qwen", skip_thinking=False)
        assert len(filtered) > 0
        assert all("qwen" in c.label.lower() for c in filtered)

    def test_filter_by_keyword_case_insensitive(self) -> None:
        filtered_lower = filter_configs(
            MODEL_CONFIGS, filter_str="qwen", skip_thinking=False
        )
        filtered_upper = filter_configs(
            MODEL_CONFIGS, filter_str="QWEN", skip_thinking=False
        )
        assert len(filtered_lower) == len(filtered_upper)

    def test_skip_thinking_removes_think_variants(self) -> None:
        filtered = filter_configs(MODEL_CONFIGS, filter_str=None, skip_thinking=True)
        assert len(filtered) > 0
        assert all("+think" not in c.label for c in filtered)

    def test_no_filter_returns_all(self) -> None:
        filtered = filter_configs(MODEL_CONFIGS, filter_str=None, skip_thinking=False)
        assert len(filtered) == len(MODEL_CONFIGS)

    def test_filter_and_skip_thinking_combined(self) -> None:
        # MODEL_CONFIGS 使用 "ds-" 前缀而非 "deepseek"，用 "ds" 过滤
        filtered = filter_configs(MODEL_CONFIGS, filter_str="ds", skip_thinking=True)
        assert len(filtered) > 0
        assert all("ds" in c.label.lower() for c in filtered)
        assert all("+think" not in c.label for c in filtered)

    def test_nonexistent_filter_returns_empty(self) -> None:
        filtered = filter_configs(
            MODEL_CONFIGS, filter_str="nonexistent_xyz", skip_thinking=False
        )
        assert filtered == []


class TestModelConfigsIntegrity:
    def test_total_count_is_18(self) -> None:
        assert len(MODEL_CONFIGS) == 18

    def test_think_variants_have_with_think_set(self) -> None:
        for cfg in MODEL_CONFIGS:
            if "+think" in cfg.label:
                assert (
                    cfg.with_think is not None
                ), f"{cfg.label}: with_think should not be None"

    def test_non_think_variants_no_extra_body(self) -> None:
        for cfg in MODEL_CONFIGS:
            if "+think" not in cfg.label:
                kwargs = build_request_kwargs(
                    cfg, cfg.no_think, temperature=0.1, timeout=30.0
                )
                assert (
                    "extra_body" not in kwargs
                ), f"{cfg.label}: should not have extra_body in no_think mode"

    def test_all_labels_unique(self) -> None:
        labels = [c.label for c in MODEL_CONFIGS]
        assert len(labels) == len(
            set(labels)
        ), "Duplicate labels found in MODEL_CONFIGS"

    def test_dashscope_configs_use_llm_api_key(self) -> None:
        for cfg in MODEL_CONFIGS:
            if "dashscope" in cfg.base_url:
                assert (
                    cfg.api_key_env == "LLM_API_KEY"
                ), f"{cfg.label}: should use LLM_API_KEY"

    def test_deepseek_official_configs_use_deepseek_api_key(self) -> None:
        for cfg in MODEL_CONFIGS:
            if cfg.base_url == "https://api.deepseek.com":
                assert (
                    cfg.api_key_env == "DEEPSEEK_API_KEY"
                ), f"{cfg.label}: should use DEEPSEEK_API_KEY"


class TestBenchmarkResultDefaults:
    def test_error_defaults_to_none(self) -> None:
        r = BenchmarkResult(label="test", thinking=False)
        assert r.error is None

    def test_numeric_fields_default_to_zero_or_none(self) -> None:
        r = BenchmarkResult(label="test", thinking=False)
        assert r.prompt_tokens == 0
        assert r.completion_tokens == 0
        assert r.ttft_ms is None
        assert r.total_ms is None
        assert r.tokens_per_sec is None
