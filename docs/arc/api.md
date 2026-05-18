# API 接口文档

REST API 前缀统一为 `/api`，WebSocket 端点为 `/ws/interview`。

---

## REST API

### 简历相关

#### `POST /api/resume/upload`

上传候选人 PDF 简历，自动触发解析和题目生成。

**请求**：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | File（PDF） | 是 | 简历 PDF 文件，仅支持 `.pdf` 格式 |
| `candidate_id` | string（query） | 否 | 指定已有候选人 ID；省略则自动创建 |

**响应** `200 OK`：

```json
{
  "candidate_id": "uuid",
  "profile": { ...CandidateProfile },
  "questions": [ ...InterviewQuestion ]
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 400 | `invalid_file_type` | 上传文件非 PDF |
| 409 | `session_error` | Agent 切换前置条件未满足 |
| 500 | `parse_error` | LLM 简历解析失败 |

**Agent 操作**：`switch_agent("resume")` → `ResumeAgent.parse_resume` → `ResumeAgent.generate_questions`

---

#### `GET /api/resume/profile`

获取候选人画像和题目清单。优先返回当前活跃会话数据，否则从数据库恢复最近一次面试记录。

**请求参数**（query）：

| 参数 | 类型 | 必填 |
|---|---|---|
| `candidate_id` | string | 是 |

**响应** `200 OK`：

```json
{
  "candidate_id": "uuid",
  "profile": { ...CandidateProfile },
  "questions": [ ...InterviewQuestion ]
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 404 | `not_found` | 候选人不存在 |

---

### 题目管理

#### `GET /api/interview/questions`

获取当前会话的题目清单。

**请求参数**（query）：

| 参数 | 类型 | 必填 |
|---|---|---|
| `candidate_id` | string | 是 |

**响应** `200 OK`：

```json
{
  "questions": [ ...InterviewQuestion ]
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 404 | `no_session` | 无对应会话 |

---

#### `PUT /api/interview/questions`

更新当前会话的题目清单（前端编辑后回写）。

**请求体** `application/json`：

```json
{
  "candidate_id": "uuid",
  "questions": [
    {
      "id": 1,
      "dimension": "系统设计",
      "question": "题目正文",
      "follow_ups": ["追问1", "追问2"],
      "difficulty": "medium"
    }
  ]
}
```

**响应** `200 OK`：

```json
{
  "questions": [ ...InterviewQuestion ]
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 404 | `no_session` | 无对应会话 |

---

### 面试生命周期

#### `POST /api/interview/start`

开始面试，切换到 `InterviewAgent`，启动音频采集和实时转写。

**请求体** `application/json`：

```json
{
  "candidate_id": "uuid",
  "trigger_mode": "auto"
}
```

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `candidate_id` | string | 必填 | 候选人 ID |
| `trigger_mode` | `"auto" \| "manual"` | `"auto"` | 追问触发模式 |

**响应** `200 OK`：

```json
{
  "session_id": "uuid",
  "stage": "interviewing"
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 409 | `session_error` | 前置条件未满足（如候选人 ID 不存在） |

**Agent 操作**：`Orchestrator.switch_agent("interview")` → 启动 `AudioManager`，绑定 WS 广播

---

#### `POST /api/interview/stop`

结束面试，切换到 `EvalAgent`（停止音频，`flush_pending_round()`）。

**请求**：无请求体

**响应** `200 OK`：

```json
{
  "session_id": "uuid",
  "stage": "evaluating",
  "total_rounds": 5
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 409 | `no_session` | 无活跃会话 |
| 409 | `session_error` | 前置条件未满足（rounds < 1） |

**Agent 操作**：`flush_pending_round()` → `Orchestrator.switch_agent("eval")` → 停止 `AudioManager`

---

#### `POST /api/interview/suggest`

手动触发一次追问建议生成（无需等待静默超时）。

**请求**：无请求体

**响应** `200 OK`：

```json
{
  "request_id": 1,
  "status": "generating"
}
```

建议内容通过 WebSocket `suggestion_delta` / `suggestion_final` 异步推送。

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 409 | `no_session` | 无活跃会话 |
| 409 | `trigger_error` | Agent 未激活或触发失败 |

**Agent 操作**：`InterviewAgent.handle_request("trigger_suggestion")`

---

#### `GET /api/interview/eval`

生成或获取评价报告。若有 `interview_id` 参数则从数据库查询历史报告；否则对当前会话调用 `EvalAgent` 生成新报告（生成完成后自动关闭会话）。

**请求参数**（query）：

| 参数 | 类型 | 必填 |
|---|---|---|
| `interview_id` | string | 否，指定时查历史 |

**响应** `200 OK`：

```json
{
  "report": {
    "id": "uuid",
    "interview_id": "uuid",
    "dimensions": [
      { "dimension": "系统设计", "score": 8.0, "comment": "...", "evidence": ["..."] }
    ],
    "overall_score": 7.5,
    "strengths": ["优势1"],
    "weaknesses": ["不足1"],
    "recommendation": "hire",
    "summary": "整体评价..."
  }
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 404 | `not_found` | 历史报告不存在 |
| 409 | `no_session` | 无活跃会话（当前模式） |
| 500 | `eval_error` | LLM 评价生成失败 |

**Agent 操作**：`EvalAgent.generate_eval()` → `memory_module.save_eval_report()` → `Orchestrator.close_session()`

---

### 会话与候选人

#### `POST /api/session/switch`

通用 Agent 切换接口。

**请求体**：

```json
{
  "target_agent": "resume"
}
```

**响应** `200 OK`：

```json
{
  "stage": "resume_analysis",
  "active_agent": "resume"
}
```

---

#### `GET /api/session/current`

获取当前会话快照（含 token 使用量）。

**响应** `200 OK`：

```json
{
  "session": {
    "id": "uuid",
    "stage": "interviewing",
    "active_agent": "interview",
    "candidate_name": "张三",
    "trigger_mode": "auto",
    "rounds_count": 3,
    "token_used": 12000,
    "token_budget": 80000
  }
}
```

若无活跃会话：`{"session": null}`

---

#### `GET /api/candidates`

搜索候选人列表。

**请求参数**（query）：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `keyword` | string | `""` | 按姓名模糊搜索 |
| `limit` | int | 20 | 每页条数 |
| `offset` | int | 0 | 偏移量 |

**响应**：

```json
{
  "candidates": [ ...CandidateProfile ],
  "total": 42
}
```

---

#### `GET /api/candidates/{candidate_id}/history`

获取候选人历史面试记录。

**响应** `200 OK`：

```json
{
  "candidate": { ...CandidateProfile },
  "interviews": [ ...InterviewSummary ]
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 404 | `not_found` | 候选人不存在 |

---

#### `GET /api/recordings/{session_id}/rounds/{round_number}`

下载指定轮次的录音文件（WAV）。

**请求参数**（query）：

| 参数 | 类型 | 说明 |
|---|---|---|
| `source` | `"candidate" \| "interviewer"` | 不指定时按顺序查找可用文件 |

**响应**：WAV 音频流 (`audio/wav`)

**错误码**：`404 not_found`（文件不存在）

---

## WebSocket `/ws/interview`

连接地址：`ws://HOST:PORT/ws/interview`

### 连接建立

连接成功后，若当前有活跃会话，服务端立即推送一条 `session_snapshot` 消息同步状态。

---

### 服务端推送消息

#### `session_snapshot`

会话状态快照，在以下时机推送：连接建立（有活跃会话时）、每个对话轮次归档时（`finalize_round()`）。

```json
{
  "type": "session_snapshot",
  "session_id": "uuid",
  "stage": "interviewing",
  "trigger_mode": "auto",
  "rounds_count": 3,
  "candidate_name": "张三"
}
```

---

#### `transcript`

实时转写片段，每个 STT 结果（含中间结果）触发一次推送。

```json
{
  "type": "transcript",
  "source": "candidate",
  "text": "我认为...",
  "is_final": true
}
```

| 字段 | 说明 |
|---|---|
| `source` | `"candidate"` 或 `"interviewer"` |
| `is_final` | `true` 为最终结果，`false` 为中间结果 |

---

#### `suggestion_delta`

追问建议流式片段，`InterviewAgent` 生成时逐字推送。

```json
{
  "type": "suggestion_delta",
  "request_id": 1,
  "delta": "你提到了..."
}
```

---

#### `suggestion_final`

追问建议生成完毕标志。

```json
{
  "type": "suggestion_final",
  "request_id": 1
}
```

---

#### `status`

操作状态通知（如触发模式切换确认）。

```json
{
  "type": "status",
  "stage": "interviewing",
  "message": "触发模式已切换为 manual"
}
```

---

#### `error`

错误通知。

```json
{
  "type": "error",
  "code": "no_session",
  "message": "无活跃会话",
  "recoverable": false
}
```

| `recoverable` | 说明 |
|---|---|
| `true` | 客户端可重试 |
| `false` | 需要重新建立会话 |

---

#### `heartbeat`

心跳响应（服务端回显客户端的 heartbeat）。

```json
{ "type": "heartbeat" }
```

---

### 客户端发送消息

#### `manual_input`

手动输入文字（代替音频采集，适用于非 Windows 或测试场景）。

```json
{
  "type": "manual_input",
  "source": "candidate",
  "text": "候选人回答内容..."
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` | `"candidate" \| "interviewer"` | 来源角色 |
| `text` | string | 文字内容 |

**处理逻辑**：通过 `TranscriptionManager.on_segment()` 进入与音频转写相同的处理流程；`source="candidate"` 时自动调用 `flush_pending_round()` 归档当前轮次。

---

#### `request_suggestion`

手动触发追问建议生成（不等待静默超时）。

```json
{
  "type": "request_suggestion"
}
```

---

#### `set_trigger_mode`

切换追问触发模式。

```json
{
  "type": "set_trigger_mode",
  "mode": "manual"
}
```

| `mode` | 说明 |
|---|---|
| `auto` | 候选人静默约 2s 后自动触发 |
| `manual` | 仅响应 `request_suggestion` |

---

#### `switch_agent`

请求切换 Agent（与 `POST /api/session/switch` 等效）。

```json
{
  "type": "switch_agent",
  "target_agent": "eval"
}
```

---

#### `heartbeat`

保活 ping，服务端原样回复。

```json
{ "type": "heartbeat" }
```

---

## 接口与 Agent 操作对应关系

| 接口 | Agent 操作 |
|---|---|
| `POST /api/resume/upload` | `switch_agent("resume")` → `ResumeAgent._parse_resume()` → `ResumeAgent._generate_questions()` |
| `GET /api/resume/profile` | 读 `session.question_plan`（内存）或 DB `get_latest_question_plan()` |
| `GET /api/interview/questions` | 读 `session.question_plan`（内存） |
| `PUT /api/interview/questions` | 直接修改 `session.question_plan`（内存） |
| `POST /api/interview/start` | `switch_agent("interview")` → `AudioManager.start()` |
| `POST /api/interview/stop` | `flush_pending_round()` → `switch_agent("eval")` → `AudioManager.stop()` |
| `POST /api/interview/suggest` | `InterviewAgent.handle_request("trigger_suggestion")` |
| `GET /api/interview/eval` | `EvalAgent._generate_eval()` → `memory_module.save_eval_report()` → `close_session()` |
| `POST /api/session/switch` | `Orchestrator.switch_agent(target)` |
| WS `manual_input` | `TranscriptionManager.on_segment()` → `SuggestionTrigger` → `InterviewAgent` |
| WS `request_suggestion` | `InterviewAgent.handle_request("trigger_suggestion")` |
| WS `set_trigger_mode` | `InterviewAgent.handle_request("set_trigger_mode")` |
