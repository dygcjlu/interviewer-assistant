# Agent 对话列表保存

## 目标

将 agent 与 LLM 之间的完整消息列表持久化到本地 jsonl 文件，供事后可视化复盘。

---

## 文件位置与命名

```
conversations/
├── main_agent.jsonl                         # MainAgent 全局唯一文件
└── interview_agent_{session_id}.jsonl       # 每次面试会话一个文件
```

`session_id` 为 UUID，天然全局唯一，不存在跨重启文件冲突。

---

## 数据格式

每行一条消息，JSON 对象，字段如下：

```jsonl
{"role": "system", "content": "...", "timestamp": "2026-05-20T12:00:00"}
{"role": "user", "content": "...", "timestamp": "2026-05-20T12:00:01"}
{"role": "assistant", "content": "...", "tool_calls": [...], "timestamp": "2026-05-20T12:00:02"}
{"role": "tool", "content": "...", "tool_call_id": "call_xxx", "timestamp": "2026-05-20T12:00:03"}
```

所有文件以 **UTF-8** 编码写入。

**system 消息的特殊处理：**
- 每次写入前检测 system prompt 是否与上次写入时相同
- 若变更（或首次），追加新的 system 行（附 timestamp）；未变更则不重复写入

> MainAgent 的 system prompt 包含动态拼接的候选人信息和用户记忆，可能在每次
> `handle_chat` 前后发生变化；记录完整的动态拼接结果，用于复盘实际生效的提示词。

---

## MainAgent 写入策略

每次 `handle_chat` 完成后，**立即追加本轮新增的消息**（追加模式）：

1. 若 system prompt 与上次记录不同，先追加一条 system 消息
2. 追加本轮的 user + assistant（+ tool，若有工具调用）消息

- 始终以追加（append）方式写入，保留完整历史
- `MainAgent` 全程单例，`main_agent.jsonl` 跨多次启动持续增长
- 不区分"面试中/无面试"场景，每轮都立即落盘，避免消息在 `_trim_history` 前丢失

---

## InterviewAgent 写入策略

- 每次面试会话（`session_id`）对应独立文件
- `on_activate` 时创建文件，写入初始 system 消息（首行）
- **日志写入封装在 `generate_suggestion` 内部**，不依赖外部调用方：
  1. 构造 user message 后，立即存入 `_history` 并追加到 logger
  2. 流式生成期间累积完整 `reply_text`
  3. 流**正常完成**后，构造 assistant message，存入 `_history` 并追加到 logger
  4. 流被 **cancel**（`CancelledError`）时，**不写入任何消息**（该轮交互已作废，不应污染日志）
- `on_deactivate` 时清空 `_history`（无需额外 flush，每次生成完毕已实时落盘）

---

## InterviewAgent 上下文管理（分阶段实现）

### 阶段一（本次实现）

目前 `generate_suggestion` 每次只看最新一轮转写，无历史记忆。

**改动：**
- 新增 `self._history: list[Message]`，在 `on_activate` 时初始化
- `generate_suggestion` 构建消息时在 system 后追加历史轮次：
  ```
  [system] [user(第1轮)] [assistant(建议A)] [user(第2轮)] [assistant(建议B)] ... [user(当前轮)]
  ```
- 每次生成**正常完成**后将 `user + assistant` 追加到 `_history`；被 cancel 时不追加
- trim 策略：保留最近 **10 轮**（20 条消息）
- `on_deactivate` 时清空 `_history`

**收益：** 避免同一次面试中重复给出相似建议。

### 阶段二（后续实现）

- 接入 `ContextManager` 做摘要压缩，应对面试轮次过多的情况
- 与 `InterviewAgent` 现有 `context_manager` 字段对齐

---

## 实现要点

### 新增 `ConversationLogger` 工具类

建议在 `src/storage/conversation_logger.py` 中实现，封装文件写入逻辑，避免各 agent 各自处理 IO：

```python
class ConversationLogger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)   # 确保目录存在
        self._path = path
        self._last_system_content: str | None = None

    async def append(self, messages: list[Message]) -> None:
        """追加写入，内部用 asyncio.to_thread 避免阻塞事件循环。"""
        await asyncio.to_thread(self._sync_write, messages)

    def _sync_write(self, messages: list[Message]) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            for msg in messages:
                line = json.dumps(msg.model_dump(exclude_none=True), ensure_ascii=False)
                f.write(line + "\n")

    async def append_with_system(self, system_content: str, messages: list[Message]) -> None:
        """若 system prompt 有变更，先写入 system 行，再追加消息。"""
        to_write: list[Message] = []
        if system_content != self._last_system_content:
            to_write.append(Message(role="system", content=system_content))
            self._last_system_content = system_content
        to_write.extend(messages)
        await self.append(to_write)
```

### MainAgent 改动点

- `__init__` 中初始化 `ConversationLogger("conversations/main_agent.jsonl")`
- `handle_chat` 末尾（`_trim_history` 之前）调用 `await self._logger.append_with_system(system_prompt, new_messages)`
- `new_messages` 为本轮新增的消息（user + assistant/tool），在追加到 `_history` 时同步收集

### InterviewAgent 改动点

- `on_activate`：初始化 `self._history`，创建 `ConversationLogger`，写入初始 system 首行
- `generate_suggestion`：
  - 构造 user message → `self._history.append(user_msg)` + `await self._logger.append([user_msg])`
  - 累积完整 `reply_text`
  - 正常完成 → `self._history.append(assistant_msg)` + `await self._logger.append([assistant_msg])`
  - `CancelledError` → 从 `_history` 移除已追加的 user_msg，不写 logger，re-raise
- `on_deactivate`：清空 `_history`，置 `_logger = None`

---

## 不纳入本次范围

- jsonl 的可视化 UI（当前目标是文件落盘，UI 后续规划）
- ResumeAgent 的对话记录（每次调用独立，不需要历史上下文）
- EvalAgent 的对话记录（每次调用一次性生成报告，结果已存入数据库，无需单独追溯）
- 跨会话的 InterviewAgent 历史恢复
