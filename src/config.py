"""应用配置 — 从 .env 文件和环境变量加载。"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── LLM 通用配置（支持任意 OpenAI 兼容平台）────────────────────────────────
    # LLM_PROVIDER 对应 src/llm/providers.py 中的注册表键值，决定平台能力判断
    # 已知值：openai_compat | qwen | deepseek | qwen_thinking
    # 接入新平台（vLLM/MiniMax/Moonshot 等无特殊能力）：直接用 openai_compat
    LLM_PROVIDER: str = "qwen"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_MODEL: str = "qwen-plus"
    LLM_TIMEOUT_SEC: float = 60.0
    LLM_MAX_RETRIES: int = 2
    LLM_ENABLE_THINKING: bool = False
    LLM_REASONING_EFFORT: str = "high"  # "high" | "max"

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

    # 火山引擎实时 ASR（豆包 BigModel）
    VOLC_APP_KEY: str = ""
    VOLC_ACCESS_KEY: str = ""
    VOLC_RESOURCE_ID: str = "volc.bigasr.sauc.duration"

    # STT 引擎选择：baidu（默认）| xunfei | volc
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

    # Qwen-VL 解析配置（PDF_PARSER=qwen_vl 时有效）
    # 已弃用：QWEN_VL_MODEL，建议迁移到 VL_LLM_MODEL；保留以向后兼容
    QWEN_VL_MODEL: str = "qwen-vl-max"
    QWEN_VL_CONCURRENCY: int = 8  # L1-5: 单份 PDF 多页并发上限，防限流

    # 多模态 VL LLM 独立配置（可选；空字符串 = 跟随主 LLM）
    # 当文本 LLM 与 VL LLM 使用不同提供商时填写（如 DeepSeek 文本 + Qwen VL）
    VL_LLM_API_KEY: str = ""
    VL_LLM_BASE_URL: str = ""
    VL_LLM_MODEL: str = ""

    @property
    def effective_vl_api_key(self) -> str:
        return self.VL_LLM_API_KEY or self.LLM_API_KEY

    @property
    def effective_vl_base_url(self) -> str:
        return self.VL_LLM_BASE_URL or self.LLM_BASE_URL

    @property
    def effective_vl_model(self) -> str:
        return self.VL_LLM_MODEL or self.QWEN_VL_MODEL or self.LLM_MODEL

    # MinerU Cloud API 配置（PDF_PARSER=mineru 时有效）
    MINERU_API_TOKEN: str = ""
    MINERU_MODEL_VERSION: str = "vlm"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings