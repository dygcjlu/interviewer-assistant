"""应用配置 — 从 .env 文件和环境变量加载。"""
from __future__ import annotations

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

    # 存储
    DB_PATH: str = "interview_assistant.db"
    RECORDINGS_DIR: str = "recordings"

    # 上下文管理
    CONTEXT_WINDOW_SIZE: int = 6
    CONTEXT_TOKEN_BUDGET: int = 80000
    CONTEXT_COMPRESSION_THRESHOLD: int = 8


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings