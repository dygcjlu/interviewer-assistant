"""测试 Settings VL LLM 配置回退逻辑。"""
import pytest
from src.config import Settings


def test_effective_vl_api_key_uses_vl_when_set():
    s = Settings(
        LLM_API_KEY="text-key",
        VL_LLM_API_KEY="vl-key",
    )
    assert s.effective_vl_api_key == "vl-key"


def test_effective_vl_api_key_falls_back_to_llm():
    s = Settings(
        LLM_API_KEY="text-key",
        VL_LLM_API_KEY="",
    )
    assert s.effective_vl_api_key == "text-key"


def test_effective_vl_base_url_uses_vl_when_set():
    s = Settings(
        LLM_BASE_URL="https://text.api.com",
        VL_LLM_BASE_URL="https://vl.api.com",
    )
    assert s.effective_vl_base_url == "https://vl.api.com"


def test_effective_vl_base_url_falls_back_to_llm():
    s = Settings(
        LLM_BASE_URL="https://text.api.com",
        VL_LLM_BASE_URL="",
    )
    assert s.effective_vl_base_url == "https://text.api.com"


def test_effective_vl_model_uses_vl_model_first():
    s = Settings(
        LLM_MODEL="text-model",
        QWEN_VL_MODEL="qwen-vl-model",
        VL_LLM_MODEL="vl-model",
    )
    assert s.effective_vl_model == "vl-model"


def test_effective_vl_model_falls_back_to_qwen_vl_model():
    s = Settings(
        LLM_MODEL="text-model",
        QWEN_VL_MODEL="qwen-vl-model",
        VL_LLM_MODEL="",
    )
    assert s.effective_vl_model == "qwen-vl-model"


def test_effective_vl_model_falls_back_to_llm_model():
    s = Settings(
        LLM_MODEL="text-model",
        QWEN_VL_MODEL="",
        VL_LLM_MODEL="",
    )
    assert s.effective_vl_model == "text-model"
