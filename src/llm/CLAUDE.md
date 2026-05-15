# llm 模块规则

## 本模块职责
封装 OpenAI 兼容 SDK，提供统一的 LLM 调用接口，支持国产模型（通义千问/DeepSeek/文心）切换。

详细设计见 `docs/arc/llm-client.md`。

## 不负责
- Prompt 构建（由 framework/prompt_builder.py 负责）
- Token 预算管理（由 framework/context.py 负责）
- 任何 Agent 业务逻辑

## 关键组件

| 文件 | 组件 |
|------|------|
| `protocol.py` | `LLMClient` Protocol、`ChatResponse`、`StreamChunk`、`ToolSchema` 定义 |
| `client.py` | `OpenAICompatibleClient` 具体实现 |
| `errors.py` | `LLMTimeoutError`、`LLMRateLimitError` 自定义异常 |

## 接口约定

- `chat()` 超时抛出 `LLMTimeoutError`，限流抛出 `LLMRateLimitError`，内置最多 `config.max_retries` 次重试。
- `chat_stream()` 返回 `AsyncIterator[StreamChunk]`，最后一个 chunk `is_final=True` 且含 token 统计。
- `count_tokens()` 基于 tiktoken，对国产模型预留 **20% 安全余量**（调用方不需要额外补偿）。
- 接受的消息类型为 `src.models.message.Message`，绝不接受原始 dict。

## 禁止事项

- 绝不在此模块直接处理音频、读写数据库。
- 绝不硬编码 base_url 或 API Key（通过 config 注入）。

## 进度记录义务

每完成一个子任务，向 `progress/llm-client.md` 追加记录。
