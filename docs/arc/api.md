# API 接口文档

REST API 前缀统一为 `/api`，WebSocket 端点为 `/ws/interview`。

---

## REST API

### 对话（MainAgent）

#### `POST /api/chat`

接收用户消息，流式转发到 MainAgent，返回 SSE 格式响应。

**请求体** `application/json`：

```json
{
  "message": "你好，请介绍候选人"
}
```

**响应**：`text/event-stream`（SSE）

每条事件为一个 JSON 对象，通过 `type` 字段区分：

```
data: {"type": "delta", "delta": "你好"}
data: {"type": "delta", "delta": "，当前候选人是..."}
data: {"type": "tool_call", "name": "dispatch_to_agent", "arguments": {...}}
data: [DONE]
```

| 事件类型 | 说明 |
|---|---|
| `delta` | LLM 流式文字片段 |
| `tool_call` | LLM 发起工具调用（透传给前端，供展示） |

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 503 | `not_ready` | MainAgent 未初始化 |

**处理逻辑**：`MainAgent.handle_chat()` → LLM 对话 + 工具调用（如 `dispatch_to_agent`、`manage_user_memory`）→ 流式返回

---

### 候选人选择

#### `POST /api/candidate/select`

选中候选人，更新 MainAgent 系统提示第 3 层上下文。

**请求体** `application/json`：

```json
{
  "candidate_id": "uuid"
}
```

**响应** `200 OK`：

```json
{
  "candidate_id": "uuid",
  "profile": { ...CandidateProfile },
  "brief": "# 面试简报\n...",
  "resume_markdown": "# 简历正文...",
  "eval_report": { ...EvalReport } 
}
```

> `brief` 为面试简报 Markdown 文本（空字符串表示尚未生成）；`eval_report` 为最近一次评价报告（`null` 表示无历史评价）。

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 404 | `not_found` | 候选人不存在 |

**处理逻辑**：加载候选人 → 创建/更新 Session → 调用 `MainAgent.set_candidate_context()` → 读取 `brief` 和 `eval_report`

---

### 简历相关

#### `POST /api/resume/upload`

上传候选人 PDF 简历，保存文件，返回 `file_path` 和 `safe_stem`。**不直接触发 LLM 解析**，解析由前端通过聊天框发送消息给 MainAgent 触发。

**请求**：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | File（PDF） | 是 | 简历 PDF 文件 |
| `candidate_id` | string（query） | 否 | 指定已有候选人 ID |
| `overwrite` | bool（query） | 否 | 是否覆盖同名候选人 |

**响应** `200 OK`：

```json
{
  "file_path": "resumes/张三.pdf",
  "safe_stem": "张三",
  "session_id": "uuid",
  "candidate_id": "uuid"
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 400 | `invalid_file_type` | 非 PDF 格式 |
| 409 | `duplicate_candidate` | 同名候选人已存在（含 `existing_candidate_id` 和 `existing_candidate_name`） |
| 409 | `interview_in_progress` | 当前面试进行中或正在评价，无法上传新简历 |
| 413 | `file_too_large` | PDF 超过 20MB 上限 |

**去重逻辑**：`candidate_id` 为空且 `overwrite=false` 时，通过 `memory.get_candidate_by_name(safe_stem)` 按文件名（去掉扩展名）精确匹配候选人姓名，存在则返回 409。

---

#### `GET /api/resume/profile`

获取候选人画像、面试简报和简历 Markdown。

**请求参数**（query）：

| 参数 | 类型 | 必填 |
|---|---|---|
| `candidate_id` | string | 是 |

**响应** `200 OK`：

```json
{
  "candidate_id": "uuid",
  "profile": { ...CandidateProfile },
  "brief": "# 面试简报\n...",
  "resume_markdown": "# 简历正文..."
}
```

---

### 面试简报

#### `GET /api/interview/brief`

获取当前会话的面试简报（优先从 session 内存读取，其次从文件读取）。

**请求参数**（query）：`candidate_id` (string, 必填)

**响应** `200 OK`：

```json
{
  "brief": "# 面试简报\n## 技术关注点\n..."
}
```

---

### 面试生命周期

#### `POST /api/interview/start`

开始面试，激活 InterviewAgent，启动音频采集和实时转写。

**请求体** `application/json`：

```json
{
  "candidate_id": "uuid",
  "trigger_mode": "auto"
}
```

**响应** `200 OK`：

```json
{
  "session_id": "uuid",
  "stage": "interviewing"
}
```

**处理逻辑**：`InterviewController.start_interview()` → 激活 InterviewAgent → 启动 AudioManager → 广播 session_snapshot

> 注意：`memory.start_interview(session)`（写 session.json）在 `brief_done` 副作用中已完成；此处 Controller 的 `start_interview()` 不再重复写入，仅负责音频启动和状态切换。

---

#### `POST /api/interview/stop`

结束面试，停止音频，flush 待归档轮次。

**响应** `200 OK`：

```json
{
  "session_id": "uuid",
  "stage": "evaluating",
  "total_rounds": 5
}
```

**处理逻辑**：`InterviewController.stop_interview()` → `flush_pending_round()` → 停止 AudioManager

---

#### `POST /api/interview/suggest`

手动触发追问建议生成。建议内容通过 WebSocket 异步推送。

---

#### `GET /api/interview/eval`

生成或获取评价报告。

**请求参数**（query）：`interview_id` (可选，指定时查历史)

**响应** `200 OK`：

```json
{
  "report": {
    "dimensions": [
      {"dimension": "系统设计", "score": 8.0, "comment": "...", "evidence": ["候选人原话..."]}
    ],
    "overall_score": 7.5,
    "strengths": [...],
    "weaknesses": [...],
    "recommendation": "hire",
    "summary": "..."
  },
  "warning": "（可选）持久化或会话关闭异常说明"
}
```

**处理逻辑（当前会话）**：`EvalAgent.handle_request("generate_eval")` → `memory.save_eval_report(report)` → `controller.close_session()`（写 transcript / 更新 index / 重置状态）

> 注意：路由层不再在调用 EvalAgent 前主动调用 `save_interview`，历史数据由 `close_session()` → `memory.finish_interview()` 统一写入。
> `close_session()` 失败时会重试 3 次，最终仍失败时响应体中附带 `warning` 字段（评价报告已生成，不重新生成）。

---

### 会话与候选人

#### `GET /api/session/current`

获取当前会话快照。

**响应** `200 OK`：

```json
{
  "session": {
    "id": "uuid",
    "stage": "interviewing",
    "active_agent": "main",
    "candidate_id": "uuid",
    "candidate_name": "张三",
    "trigger_mode": "auto",
    "rounds_count": 3,
    "token_used": 12000,
    "token_budget": 80000
  }
}
```

---

#### `GET /api/candidates`

搜索候选人列表。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `keyword` | string | `""` | 按姓名模糊搜索（读 candidates/index.md） |
| `limit` | int | 20 | 每页条数 |
| `offset` | int | 0 | 偏移量 |

---

#### `GET /api/candidates/{candidate_id}/history`

获取候选人历史面试记录。

---

#### `DELETE /api/candidates/{candidate_id}`

删除候选人（递归删除 `candidates/{id}/` 目录及所有文件）。

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 404 | `not_found` | 候选人不存在 |
| 409 | `candidate_in_use` | 候选人当前正在面试中，无法删除 |

---

#### `GET /api/recordings/{session_id}/rounds/{round_number}`

下载指定轮次的录音文件（WAV）。

**请求参数**（query）：`source` (可选，`candidate` 或 `interviewer`，不指定时返回第一个存在的文件)

---

### WAL 恢复（recovery）

进程异常退出后，可能存在 `rounds.jsonl` WAL 残留（面试数据已写入但 `finish_interview` 未完成）。Recovery API 支持扫描和恢复这些残留数据。

#### `GET /api/recovery/scan`

列出所有未完成归档的残留 WAL（孤儿面试）。

**响应** `200 OK`：

```json
{
  "orphans": [
    {"candidate_id": "uuid", "interview_id": "uuid", "rounds_count": 5}
  ],
  "count": 1
}
```

---

#### `POST /api/recovery/finish`

从 WAL 恢复指定面试：重建 rounds → 写 `transcript.md` → 归档 WAL。

**请求体** `application/json`：

```json
{
  "candidate_id": "uuid",
  "interview_id": "uuid"
}
```

**响应** `200 OK`：

```json
{
  "recovered_rounds": 5,
  "candidate_id": "uuid",
  "interview_id": "uuid"
}
```

---

#### `POST /api/recovery/discard`

丢弃残留 WAL（用户确认不需要恢复时）。

**请求体** `application/json`：`{"candidate_id": "uuid", "interview_id": "uuid"}`

---

### 健康检查与监控

#### `GET /api/health`

轻量健康探针，供 Docker healthcheck / 进程守护脚本使用。`controller` 或 `memory` 任意未就绪时返回 503。

**响应** `200 OK`：

```json
{
  "status": "ok",
  "controller": true,
  "memory": true
}
```

---

#### `GET /api/metrics`

返回进程级累积 LLM 指标（token 用量 / 请求次数 / 延迟百分位数）。

---

## WebSocket `/ws/interview`

连接地址：`ws://HOST:PORT/ws/interview`

### 服务端推送消息

| 类型 | 说明 |
|---|---|
| `session_snapshot` | 会话状态快照（含 `brief` 字段） |
| `transcript` | 实时转写片段（source + text + is_final） |
| `suggestion_delta` | 追问建议流式片段（含 `request_id` + `delta`） |
| `suggestion_final` | 追问建议完成标志（含 `request_id` + `text` + `skipped`） |
| `audio_status` | 音频管道状态变化通知 |
| `status` | 操作状态通知 |
| `error` | 错误通知（含 `recoverable` 字段） |
| `heartbeat` | 心跳响应 |

### 客户端发送消息

| 类型 | 说明 |
|---|---|
| `request_suggestion` | 手动触发追问建议 |
| `set_trigger_mode` | 切换追问触发模式（auto/manual） |
| `switch_agent` | 兼容消息，映射到 start_interview / stop_interview |
| `heartbeat` | 保活 ping |

> `manual_input`（手动文字输入）已移除，音频转写是唯一的文字来源。

---

## 接口与 Agent 操作对应关系

| 接口 | 处理逻辑 |
|---|---|
| `POST /api/chat` | `MainAgent.handle_chat()` → LLM + 工具调用 |
| `POST /api/candidate/select` | `MainAgent.set_candidate_context()` |
| `POST /api/resume/upload` | 保存 PDF 文件，返回 file_path（解析由聊天触发） |
| `GET /api/resume/profile` | `memory.get_candidate()` + session 缓存 + `memory.get_resume_markdown()` |
| `GET /api/interview/brief` | session.interview_brief 或 `memory.get_brief()` |
| `POST /api/interview/start` | `InterviewController.start_interview()` → AudioManager.start() |
| `POST /api/interview/stop` | `InterviewController.stop_interview()` → AudioManager.stop() |
| `POST /api/session/switch` | 兼容接口：interview → start_interview()，eval → stop_interview() |
| `POST /api/interview/suggest` | `controller.interview_agent.handle_request("trigger_suggestion")` |
| `GET /api/interview/eval` | `EvalAgent.handle_request("generate_eval")` → `memory.save_eval_report()` → `controller.close_session()` |
| `GET /api/recovery/scan` | `memory.scan_orphan_wal()` |
| `POST /api/recovery/finish` | `memory.recover_interview_from_wal()` |
| `POST /api/recovery/discard` | `memory.discard_orphan_wal()` |
| `GET /api/health` | 检查 controller + memory 是否就绪 |
| `GET /api/metrics` | `Metrics.get().to_dict()` |
| WS `request_suggestion` | `controller.interview_agent.handle_request("trigger_suggestion")` |
| WS `set_trigger_mode` | `controller.interview_agent.handle_request("set_trigger_mode")` |
| WS `switch_agent` | 兼容消息：interview → start_interview()，eval → stop_interview() |
