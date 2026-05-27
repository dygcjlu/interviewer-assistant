"""应用配置 — 从 .env 文件和环境变量加载。"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM (通义千问 / OpenAI 兼容)
    QWEN_API_KEY: str = ""
    QWEN_API_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-plus"
    LLM_TIMEOUT_SEC: float = 30.0
    LLM_MAX_RETRIES: int = 2

    # HTTP 服务
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DEBUG: bool = False

    # 存储（M1-2: 统一为 Path 类型，避免调用方各自 Path(...) 包裹的混乱）
    CANDIDATES_DIR: Path = Path("candidates")
    RECORDINGS_DIR: Path = Path("recordings")

    # 上下文管理
    CONTEXT_WINDOW_SIZE: int = 6
    CONTEXT_TOKEN_BUDGET: int = 80000
    CONTEXT_COMPRESSION_THRESHOLD: int = 8

    # ResumeAgent ReAct 模式最大工具调用轮次
    RESUME_AGENT_MAX_TOOL_ROUNDS: int = 15

    # 百度 ASR（实时语音识别）
    BAIDU_APP_ID: str = ""
    BAIDU_API_KEY: str = ""
    BAIDU_SECRET_KEY: str = ""

    # 讯飞实时语音转写大模型
    XUNFEI_APP_ID: str = ""
    XUNFEI_ACCESS_KEY_ID: str = ""
    XUNFEI_ACCESS_KEY_SECRET: str = ""

    # STT 引擎选择：baidu（默认）| xunfei
    STT_ENGINE: str = "baidu"

    # 调试：用脚本模拟音频，跳过真实采集和 STT
    MOCK_AUDIO: bool = False
    MOCK_AUDIO_SCRIPT: str = "data/mock_script.json"

    # S-16: 敏感日志开关 — 默认 False，不把 LLM messages 完整内容写入日志文件。
    # 开启（True）时可在 logs/app.log 中看到完整 LLM 消息体，便于本地调试；
    # 生产 / 长期运行时应保持 False，避免简历、面试对话等敏感内容落盘。
    LOG_SENSITIVE: bool = False

    # PDF 解析引擎：pymupdf | qwen_vl | mineru
    PDF_PARSER: str = "qwen_vl"

    # Qwen-VL 解析配置（PDF_PARSER=qwen_vl 时有效，复用 QWEN_API_KEY）
    QWEN_VL_MODEL: str = "qwen-vl-max"
    QWEN_VL_CONCURRENCY: int = 8  # L1-5: 单份 PDF 多页并发上限，防限流

    # MinerU Cloud API 配置（PDF_PARSER=mineru 时有效）
    MINERU_API_TOKEN: str = ""
    MINERU_MODEL_VERSION: str = "vlm"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings