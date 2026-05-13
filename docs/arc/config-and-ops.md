# 配置与运维

## 1. 配置模块（Config）

采用**配置文件 + 环境变量双层架构**，基础配置与敏感信息分离管理。

### 1.1 双层配置加载

| 层级 | 来源 | 内容 | 示例 |
|------|------|------|------|
| 基础配置 | `config.yaml` | 业务参数、模型选择、阈值等非敏感配置 | 滑动窗口大小、沉默触发阈值、日志级别 |
| 敏感配置 | `.env` 文件 | API Key、密钥等敏感信息 | LLM API Key、百度 STT 密钥 |

加载优先级：`.env` 中的值覆盖 `config.yaml` 中的同名项（环境变量优先）。

### 1.2 关键配置项

```yaml
# config.yaml

llm:
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-chat"
  timeout_sec: 10
  max_retries: 2

context:
  window_size: 6                    # 滑动窗口保留轮次数
  token_budget: 80000               # 单次请求 token 预算
  token_safety_margin: 0.2          # token 预算安全余量（20%）
  compression_round_threshold: 8    # 触发压缩的轮次阈值

suggestion:
  silence_threshold_sec: 2.0        # 沉默触发阈值（秒）
  min_trigger_interval_sec: 5.0     # 自动触发最小间隔（秒）
  trigger_mode: "auto"              # 默认触发模式 "auto" | "manual"

audio:
  sample_rate: 16000
  channels: 1

stt:
  provider: "baidu"                 # STT 提供商
  max_reconnect_attempts: 3

storage:
  db_path: "data/interview.db"
  recordings_dir: "recordings"

logging:
  level: "INFO"
  max_file_size_mb: 10
  backup_count: 5
```

```bash
# .env

LLM_API_KEY=sk-xxxxxxxxxxxx
BAIDU_STT_APP_ID=12345678
BAIDU_STT_API_KEY=xxxxxxxxxxxx
BAIDU_STT_SECRET_KEY=xxxxxxxxxxxx
```

### 1.3 实现方式

使用 `pydantic-settings` + PyYAML 加载，提供类型安全的配置访问：

```python
class AppConfig(BaseSettings):
    """双层配置：config.yaml 为基础，.env 覆盖敏感项"""
    llm: LLMConfig
    context: ContextConfig
    suggestion: SuggestionConfig
    # ...

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "AppConfig":
        """加载 YAML 基础配置，再用 .env 覆盖敏感项"""
        ...
```

---

## 2. 可观测性设计

### 2.1 日志

采用 Python 标准 `logging` + `RotatingFileHandler`，分两个日志文件：

| 文件 | 内容 | 策略 |
|------|------|------|
| `logs/agent.log` | Agent 决策、LLM 调用、工具执行、压缩事件等所有运行日志 | 轮转，单文件最大 10MB，保留 5 份 |
| `logs/error.log` | WARNING 及以上级别的错误日志 | 轮转，单文件最大 5MB，保留 3 份 |

日志格式：`[时间戳] [级别] [模块] 消息`，敏感信息（API Key）在 Formatter 层过滤。

### 2.2 面试轨迹（Trajectory）

每场面试在 `trajectories/` 目录下生成一个 JSONL 文件（`{session_id}.jsonl`），每行记录一轮 Agent 的完整输入/输出：

```jsonc
// trajectories/{session_id}.jsonl  —— 每行一条 JSON
{
    "round": 3,
    "timestamp": "2026-05-12T21:00:00",
    "trigger": "auto_silence",           // "auto_silence" | "manual"
    "messages_sent": [...],              // 发给 LLM 的完整 messages 数组
    "response": "可以追问：Redis 集群的分片策略...",
    "prompt_tokens": 4500,
    "completion_tokens": 120,
    "compression_triggered": false
}
```

轨迹文件用于：
- 离线分析 token 消耗分布
- 复盘上下文管理效果（压缩前后的 prompt 对比）
- 调试 LLM 输出异常

### 2.3 Token 追踪（TokenTracker）

`TokenTracker` 作为 `ContextManager` 的协作组件，在每次 LLM 调用后更新统计：

- **实时**：通过 WebSocket 推送 `token_usage` 消息到前端（参见 [Web 层](./web-layer.md) WebSocket 协议）
- **持久化**：写入 `TokenUsage` 表（参见 [记忆与数据持久化](./memory-and-storage.md) 数据模型）
- **预算告警**：当 token 用量超过预算的 80% 时，在 `agent.log` 中记录 WARNING

```python
class TokenTracker:
    """Token 用量追踪器 — ContextManager 的协作组件"""

    def __init__(self, budget: int, ws_sender: Callable[[dict], Awaitable[None]]): ...

    def record_usage(self, prompt_tokens: int, completion_tokens: int,
                     round_number: int) -> None:
        """记录一次 LLM 调用的 token 消耗"""

    async def push_usage_to_frontend(self) -> None:
        """通过 WebSocket 推送 token_usage 消息到前端"""

    def check_budget_warning(self) -> bool:
        """检查是否超过预算 80%，超过则记录 WARNING，返回 True"""

    @property
    def total_prompt_tokens(self) -> int: ...

    @property
    def total_completion_tokens(self) -> int: ...

    @property
    def total_tokens(self) -> int: ...

    def to_usage_records(self, interview_id: str) -> list[dict]:
        """导出所有记录，用于写入 TokenUsage 表"""
```

---

## 3. 降级与容错

### 3.1 异常层次

项目级异常定义，底层异常在各自模块内处理或向上抛出，Web 层统一转换为用户友好的响应：

```
InterviewError（基类）
├── STTError
│   ├── STTConnectionError        # STT WebSocket 连接失败/断开
│   └── STTRecognitionError       # 识别结果异常
├── LLMError
│   ├── LLMTimeoutError           # LLM API 超时
│   ├── LLMRateLimitError         # 限流
│   └── LLMResponseError          # 返回内容异常
├── AudioError
│   ├── AudioDeviceError          # 音频设备不可用
│   └── RecordingError            # 录音写入失败
├── ResumeParseError              # 简历解析失败
└── SessionError                  # 会话状态错误（如前置条件不满足）
```

### 3.2 Web 层统一错误响应

REST API 错误格式：

```json
{
    "error": {
        "code": "resume_parse_failed",
        "message": "简历解析失败：PDF 文件损坏",
        "recoverable": true
    }
}
```

WebSocket 错误通过 `error` 类型消息推送（参见 [Web 层](./web-layer.md) WebSocket 下行消息协议）。

### 3.3 前端 WebSocket 重连与状态恢复

前端 WebSocket 断线后的恢复机制：

1. **自动重连**：WebSocket 断开后自动重连，指数退避（1s → 2s → 4s → 最大 30s）
2. **状态同步**：重连成功后，后端自动推送 `session_snapshot` 消息，前端据此恢复到正确状态
3. **REST 兜底**：增加 `GET /api/session/current` 接口，前端可在任意时刻主动获取当前会话完整状态

### 3.4 降级策略表

| 异常场景 | 降级策略 |
|----------|----------|
| STT 连接断开 | 自动重连（最多 3 次），失败后通过 WS 推送 error 消息，前端提示并切换为手动输入模式 |
| LLM API 超时/限流 | 前端显示"建议生成中..."，超时 10 秒跳过本轮建议，不阻塞面试 |
| 音频设备不可用 | 启动时检测，无可用设备降级为纯手动输入模式 |
| LLM 返回异常 | 丢弃本轮建议，前端不展示，记录日志 |
| 简历解析失败 | 提示失败原因，面试官可手动输入候选人关键信息 |
| 录音写入失败 | 记录日志告警，不影响面试主流程继续 |
| Token 预算耗尽 | 强制压缩摘要区，极端情况下仅保留固定区 + 最近 2 轮 |
| WebSocket 断线 | 前端自动重连 + 状态快照恢复，面试数据不丢失 |
