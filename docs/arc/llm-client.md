# LLM Client

## 1. 接口定义

基于 OpenAI SDK 兼容模式，统一封装。所有 Agent 通过此客户端与 LLM 交互。

> `Message`、`ToolCallInfo` 类型定义见 [共享数据结构](./data-models.md)

```python
class LLMClient:
    """LLM 客户端 — OpenAI SDK 兼容模式"""

    def __init__(self, config: LLMConfig): ...

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.7,
        timeout_sec: float | None = None,
    ) -> ChatResponse:
        """同步请求（等待完整响应）
        超时抛出 LLMTimeoutError，限流抛出 LLMRateLimitError
        内置重试（最多 config.max_retries 次）"""

    async def chat_stream(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        timeout_sec: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式请求（逐 token 返回）
        用于实时追问建议推送到前端"""

    def count_tokens(self, messages: list[Message]) -> int:
        """基于 tiktoken 预估 token 数（发送前粗估用）
        对国产模型中文分词有偏差，预留 20% 安全余量"""
```

### 返回类型

```python
@dataclass
class ChatResponse:
    """chat() 的返回值"""
    content: str                           # LLM 回复文本
    tool_calls: list[ToolCallInfo] | None = None  # 工具调用请求（若有）
    prompt_tokens: int = 0                 # API 返回的实际 prompt token 数
    completion_tokens: int = 0             # API 返回的实际 completion token 数

@dataclass
class StreamChunk:
    """chat_stream() 的每个 yield 项"""
    delta: str                             # 本次增量文本
    is_final: bool = False                 # 是否为最后一个 chunk
    prompt_tokens: int | None = None       # 仅 is_final=True 时有值
    completion_tokens: int | None = None   # 仅 is_final=True 时有值

@dataclass
class ToolSchema:
    """传入 LLM 的工具定义（对齐 OpenAI function calling 格式）"""
    type: str = "function"
    function: ToolFunction

@dataclass
class ToolFunction:
    name: str
    description: str
    parameters: dict                       # JSON Schema
```

## 2. 关键设计

- **模型切换**：通过配置 `base_url` 切换模型提供商（通义千问/DeepSeek/文心），所有国产模型均通过 OpenAI 兼容接口接入
- **流式输出**：用于实时追问建议推送到前端，参见 [建议生成触发机制](./suggestion-trigger.md)
- **重试与超时降级**：内置重试 + 超时降级（10 秒超时跳过本轮建议，不阻塞面试）

## 3. Token 计数双轨方案

| 阶段 | 方法 | 用途 |
|------|------|------|
| 发送前 | `tiktoken` 本地预估 | 控制上下文 token 预算，决定是否触发压缩 |
| 发送后 | API 返回 `usage` 字段 | 实际消耗统计，写入 `TokenUsage` 表 |

tiktoken 是 OpenAI 的分词器，对国产模型（尤其中文内容）存在偏差，因此 token 预算预留 20% 安全余量（如 128K 窗口设 80K 预算而非 100K）。

> 参见 [上下文管理与 Prompt 构建](./context-and-prompt.md) 了解 token 预算如何驱动上下文压缩。

---

## 4. 设计决策

### 决策 5: 简历 OCR 方案

```
├── 方案 A: PyMuPDF 文本提取 + 回退到 LLM 视觉模型处理截图类 PDF
├── 方案 B: 本地 OCR（Tesseract）
└── 选择: 方案 A
    理由: 大多数简历是文本 PDF，截图 PDF 用 LLM 视觉能力处理更准确、免部署。
```

### 决策 6: Token 计数

```
├── 方案 A: tiktoken 本地预估
├── 方案 B: 模型 API 返回 usage 字段
└── 选择: 两者结合
    理由: tiktoken 做发送前粗估以控制预算，API 返回值做发送后实际统计以追踪消耗。
         tiktoken 对国产模型的中文分词有偏差，预算预留 20% 安全余量补偿。
```
