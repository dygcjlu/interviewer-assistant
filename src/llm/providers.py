"""LLM 平台能力声明 — ProviderProfile 注册表。

各平台均兼容 OpenAI API 格式，但发送的请求参数因平台而异。
ProviderProfile 结构化声明平台能力，驱动 Client 决定发哪些参数，
避免在 Client 中硬编码 if provider == "xxx" 分支。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderProfile:
    name: str

    # ── 思考模式能力 ──────────────────────────────────────────────────────────
    # 该平台是否支持思考模式（由 LLM_ENABLE_THINKING 运行时开关进一步控制）
    supports_thinking: bool = False

    # 思考模式下是否禁止传 temperature（DeepSeek 要求；Qwen3 不要求）
    thinking_disables_temperature: bool = False

    # 工具调用后续请求是否必须回传 reasoning_content（DeepSeek 强制，否则返回 400）
    thinking_requires_reasoning_content: bool = False

    # 启用思考时需附加到请求 extra_body 的字段（各平台格式不同）
    thinking_extra_body: dict = field(default_factory=dict)


# 内置注册表
PROFILES: dict[str, ProviderProfile] = {
    # 通用 OpenAI 兼容：vLLM 自部署、MiniMax、Moonshot 等平台无需特殊处理
    "openai_compat": ProviderProfile(name="openai_compat"),
    # 阿里 Qwen 非思考版（qwen-plus、qwen-turbo 等）
    "qwen": ProviderProfile(name="qwen"),
    # DeepSeek 思考模式 —— 三个约束全部开启
    "deepseek": ProviderProfile(
        name="deepseek",
        supports_thinking=True,
        thinking_disables_temperature=True,
        thinking_requires_reasoning_content=True,
        thinking_extra_body={"thinking": {"type": "enabled"}},
    ),
    # Qwen3 思考版 —— 支持思考，但 temperature 仍可用，不强制回传
    "qwen_thinking": ProviderProfile(
        name="qwen_thinking",
        supports_thinking=True,
        thinking_disables_temperature=False,
        thinking_requires_reasoning_content=False,
        thinking_extra_body={"enable_thinking": True},
    ),
}


def get_profile(provider: str) -> ProviderProfile:
    """获取指定平台的 ProviderProfile。未知平台返回通用兼容 Profile。"""
    return PROFILES.get(provider, PROFILES["openai_compat"])
