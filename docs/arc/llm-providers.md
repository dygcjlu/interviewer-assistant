# LLM 多平台接入设计

本文档描述面试助手如何通过 `ProviderProfile` 机制统一管理多个 LLM 平台（Qwen、DeepSeek、vLLM 自部署、MiniMax 等）的差异，实现「换平台只改配置」。

配置入口：`get_settings()`（`src/config.py`）→ `LLMConfig`（`src/llm/config.py`）→ `OpenAICompatibleClient`（`src/llm/client.py`）。

---

## 问题背景

所有目标平台均兼容 OpenAI API 格式，协议层无差异，但请求参数因平台而异：

| 差异点 | 示例 |
|---|---|
| 是否发 `temperature` | DeepSeek 思考模式下禁止传，其他平台均支持 |
| 思考模式开关格式 | DeepSeek 用 `extra_body.thinking`，Qwen3 用 `extra_body.enable_thinking` |
| 是否发 `reasoning_effort` | DeepSeek / Qwen3 思考版支持，普通模型无此参数 |
| 是否在消息体中回传 `reasoning_content` | DeepSeek 工具调用时**强制要求**，否则 400；其他平台不需要 |

这些差异是「同一协议下不同平台的能力集合」，由 `ProviderProfile` 声明，而非在 Client 里硬编码厂商分支。

---

## 核心设计：ProviderProfile

**文件**：`src/llm/providers.py`

`OpenAICompatibleClient` 读取 Profile 决定发什么参数。

### 数据结构

```python
@dataclass(frozen=True)
class ProviderProfile:
    name: str
    supports_thinking: bool = False
    thinking_disables_temperature: bool = False
    thinking_requires_reasoning_content: bool = False
    thinking_extra_body: dict = field(default_factory=dict)
```

### 内置注册表

| `LLM_PROVIDER` | 含义 |
|---|---|
| `openai_compat` | 通用兼容（vLLM / MiniMax / Moonshot 等） |
| `qwen` | 阿里 Qwen 非思考版（默认） |
| `deepseek` | DeepSeek 思考模式（禁 temperature、强制回传 reasoning_content） |
| `qwen_thinking` | Qwen3 思考版（`enable_thinking`，不强制回传） |

未知 `LLM_PROVIDER` 时 `get_profile()` 回退到 `openai_compat`。

---

## 整体数据流

```
.env
  LLM_PROVIDER / LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
  LLM_ENABLE_THINKING / LLM_REASONING_EFFORT
        │
        ▼
  Settings（src/config.py）
        ▼
  LLMConfig（src/llm/config.py）
        ▼
  providers.get_profile(provider)
        ▼
  OpenAICompatibleClient
        │ 读 Profile 决定 temperature / reasoning_effort / extra_body / reasoning_content
        ▼
  OpenAI SDK → HTTP → LLM API
```

---

## Client 决策逻辑（要点）

```python
profile = get_profile(self._config.provider)
thinking_on = self._config.enable_thinking and profile.supports_thinking

# temperature：thinking 且平台禁止时不发
# thinking：附加 reasoning_effort + profile.thinking_extra_body
# reasoning_content：仅当 thinking_requires_reasoning_content 且工具调用链路需要时回传
```

消息模型：`src/models/message.Message` 含可选 `reasoning_content`；`ChatResponse`（`src/llm/protocol.py`）同步携带该字段。工具循环（`BaseAgent._run_with_tools`）在 assistant 消息中保存 reasoning，以满足 DeepSeek 等平台要求。

---

## 扩展规则

### 无特殊能力的新平台

只改 `.env`：

```ini
LLM_PROVIDER=openai_compat
LLM_API_KEY=<key>
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL=...
LLM_ENABLE_THINKING=false
```

### 思考模式格式不同的新平台

在 `PROFILES` 增加一条 `ProviderProfile`，Client 无需改动；`.env` 设置对应 `LLM_PROVIDER`。

### Profile 无法描述的新能力

给 `ProviderProfile` 加字段（带默认值）并在 Client 增加对应分支。

---

## 各 Agent 与思考模式

| Agent | 建议 | 原因 |
|---|---|---|
| ResumeAgent | 不开启 | 工具调用链路长，延迟敏感 |
| InterviewAgent | 不开启 | 实时追问，低延迟优先 |
| EvalAgent | 可开启 | 非实时，质量优先 |
| MainAgent | 按需 | 一般无需深度推理 |

当前由全局 `LLM_ENABLE_THINKING` 控制。

---

## 多模态（VL）LLM 独立配置

PDF 解析（`QwenVLParser`）可使用与文本 LLM 不同的提供商。

| 变量 | 说明 | 默认 |
|---|---|---|
| `VL_LLM_API_KEY` | VL API Key | 空 → `LLM_API_KEY` |
| `VL_LLM_BASE_URL` | VL Base URL | 空 → `LLM_BASE_URL` |
| `VL_LLM_MODEL` | VL 模型名 | 空 → `QWEN_VL_MODEL` → `LLM_MODEL` |

有效值由 `Settings.effective_vl_*` 属性计算。典型场景：DeepSeek 文本 + Qwen VL 时填写全部 `VL_LLM_*`。
