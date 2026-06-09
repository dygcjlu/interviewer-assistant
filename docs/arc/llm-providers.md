# LLM 多平台接入设计

本文档描述面试助手如何通过 `ProviderProfile` 机制统一管理多个 LLM 平台（Qwen、DeepSeek、vLLM 自部署、MiniMax 等）的差异，实现"换平台只改配置"的目标。

---

## 问题背景

所有目标平台均兼容 OpenAI API 格式，协议层无差异，但发送的请求内容因平台而异：

| 差异点 | 示例 |
|---|---|
| 是否发 `temperature` | DeepSeek 思考模式下禁止传，其他平台均支持 |
| 思考模式开关格式 | DeepSeek 用 `extra_body.thinking`，Qwen3 用 `extra_body.enable_thinking` |
| 是否发 `reasoning_effort` | DeepSeek/Qwen3 思考版支持，普通模型无此参数 |
| 是否在消息体中回传 `reasoning_content` | DeepSeek 工具调用时**强制要求**，否则返回 400；其他平台不需要 |

这些差异本质上是"同一协议下不同平台的能力集合不同"，而非协议不兼容。

---

## 核心设计：ProviderProfile

`ProviderProfile` 是对某个平台能力的结构化声明。`OpenAICompatibleClient` 读取 Profile 来决定发什么参数，而不是硬编码 `if provider == "deepseek"` 的分支。

### 数据结构（`src/llm/providers.py`）

```python
@dataclass(frozen=True)
class ProviderProfile:
    name: str

    # ── 思考模式能力 ──────────────────────────────────────
    # 该平台是否支持思考模式（由 LLM_ENABLE_THINKING 运行时开关进一步控制）
    supports_thinking: bool = False

    # 思考模式下是否禁止传 temperature（DeepSeek 要求；Qwen3 不要求）
    thinking_disables_temperature: bool = False

    # 工具调用后续请求是否必须回传 reasoning_content（DeepSeek 强制，否则 400）
    thinking_requires_reasoning_content: bool = False

    # 启用思考时需附加到请求 extra_body 的字段（各平台格式不同）
    thinking_extra_body: dict = field(default_factory=dict)
```

### 内置注册表

```python
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
```

---

## 整体数据流

```
.env
  LLM_PROVIDER=deepseek          ← 选择平台
  LLM_API_KEY=sk-xxx
  LLM_BASE_URL=https://api.deepseek.com
  LLM_MODEL=deepseek-v4-pro
  LLM_ENABLE_THINKING=true       ← 运行时开关：是否激活思考模式
  LLM_REASONING_EFFORT=high
        │
        ▼
  Settings（src/config.py）
        │ LLM_PROVIDER / LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
        │ LLM_ENABLE_THINKING / LLM_REASONING_EFFORT
        ▼
  LLMConfig（src/llm/config.py）
        │
        ├── provider = "deepseek"
        ├── enable_thinking = True
        │
        ▼
  providers.get_profile("deepseek")
        │
        ▼
  ProviderProfile（声明卡片）
        │ supports_thinking=True
        │ thinking_disables_temperature=True
        │ thinking_requires_reasoning_content=True
        │ thinking_extra_body={"thinking": {"type": "enabled"}}
        │
        ▼
  OpenAICompatibleClient（唯一 Client 实现）
        │ 读 Profile 决定：
        │   - 是否发 temperature
        │   - 是否发 reasoning_effort + extra_body
        │   - 构建消息时是否包含 reasoning_content
        ▼
  OpenAI SDK → HTTP → LLM API
```

---

## Client 决策逻辑（伪代码）

```python
profile = get_profile(self._config.provider)
thinking_on = self._config.enable_thinking and profile.supports_thinking

# 1. temperature：thinking 模式且平台禁止时不发
if not (thinking_on and profile.thinking_disables_temperature):
    kwargs["temperature"] = temperature

# 2. 思考模式参数
if thinking_on:
    kwargs["reasoning_effort"] = self._config.reasoning_effort
    if profile.thinking_extra_body:
        kwargs["extra_body"] = profile.thinking_extra_body

# 3. 消息体中的 reasoning_content
#    仅当平台要求且当前处于思考模式 + 有工具调用时才附加
def _include_reasoning_content(self) -> bool:
    profile = get_profile(self._config.provider)
    return (
        self._config.enable_thinking
        and profile.supports_thinking
        and profile.thinking_requires_reasoning_content
    )
```

---

## 变更文件清单

| 文件 | 变更内容 |
|---|---|
| `src/llm/providers.py` | **新建**：ProviderProfile 定义 + 内置注册表 |
| `src/config.py` | 通用 LLM_* 配置，移除 QWEN_* 特定字段 |
| `src/llm/config.py` | 新增 `provider`、`enable_thinking`、`reasoning_effort` |
| `src/models/message.py` | 新增 `reasoning_content: str \| None` |
| `src/llm/protocol.py` | `ChatResponse` 新增 `reasoning_content` |
| `src/llm/client.py` | Profile 驱动发参逻辑；提取/回传 `reasoning_content` |
| `src/agents/base.py` | `_run_with_tools` 在 assistant 消息中保存 `reasoning_content` |
| `src/main.py` | 使用 `settings.LLM_*` 字段初始化 LLMConfig |
| `.env` | 改为 LLM_* 通用变量 |

---

## 扩展规则

### 接新平台，无特殊能力（vLLM、MiniMax、Moonshot 等）

只需改 `.env`，代码零改动：

```ini
LLM_PROVIDER=openai_compat
LLM_API_KEY=<key>
LLM_BASE_URL=http://localhost:8000/v1    # vLLM 自部署地址
LLM_MODEL=Qwen2.5-72B-Instruct
LLM_ENABLE_THINKING=false
```

### 接新平台，思考模式格式与已有平台不同

在 `src/llm/providers.py` 的 `PROFILES` 字典中添加一条记录，**Client 代码不需要改动**：

```python
"new_platform": ProviderProfile(
    name="new_platform",
    supports_thinking=True,
    thinking_disables_temperature=True,
    thinking_requires_reasoning_content=False,
    thinking_extra_body={"thought_mode": "on"},   # 该平台的特有格式
),
```

然后在 `.env` 中设置 `LLM_PROVIDER=new_platform`。

### 出现 Profile 无法描述的全新能力

在 `ProviderProfile` 中添加新字段（frozen dataclass，添加字段是 backward compatible），在 Client 中加对应逻辑分支，已有平台的 Profile 记录无需改动（新字段有默认值）。

---

## 各 Agent 的思考模式建议

| Agent | 建议 | 原因 |
|---|---|---|
| `ResumeAgent` | 不开启 | 使用工具调用，已有超时问题，思考模式只会更慢 |
| `InterviewAgent`（追问建议） | 不开启 | 实时响应，低延迟优先 |
| `EvalAgent`（评价报告） | **可开启** | 非实时，推理质量比速度重要 |
| `MainAgent` | 按需 | 一般无需深度推理 |

目前思考模式通过 `LLM_ENABLE_THINKING` 全局控制，未来如需 Agent 级别的独立控制，可在各 Agent 的 `execute` 方法中传入 `timeout_sec` 时同步传入 `enable_thinking` 覆盖参数（Protocol 已预留 `timeout_sec` 可选参数的扩展模式）。

---

## 多模态（VL）LLM 独立配置

PDF 简历解析（`QwenVLParser`）使用多模态视觉语言模型，与文本 LLM 可以是不同提供商。

### 配置变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `VL_LLM_API_KEY` | VL 模型的 API Key | 空（回退到 `LLM_API_KEY`）|
| `VL_LLM_BASE_URL` | VL 模型的接入点 | 空（回退到 `LLM_BASE_URL`）|
| `VL_LLM_MODEL` | VL 模型名称 | 空（回退到 `QWEN_VL_MODEL`，再回退到 `LLM_MODEL`）|

### 回退规则

```
effective_vl_api_key:   VL_LLM_API_KEY → LLM_API_KEY
effective_vl_base_url:  VL_LLM_BASE_URL → LLM_BASE_URL
effective_vl_model:     VL_LLM_MODEL → QWEN_VL_MODEL → LLM_MODEL
```

### 典型场景

**同一提供商（Qwen 文本 + Qwen VL）** — `VL_LLM_*` 留空，自动复用：

```ini
LLM_PROVIDER=qwen
LLM_API_KEY=sk-aliyun-xxx
LLM_MODEL=qwen3.7-plus
QWEN_VL_MODEL=qwen3-vl-plus   # 或 VL_LLM_MODEL=qwen3-vl-plus
```

**不同提供商（DeepSeek 文本 + Qwen VL）** — 填写 `VL_LLM_*`：

```ini
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-deepseek-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro

VL_LLM_API_KEY=sk-aliyun-xxx
VL_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VL_LLM_MODEL=qwen3-vl-plus
```
