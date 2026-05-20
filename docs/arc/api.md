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

```
data: {"delta": "你好"}
data: {"delta": "，当前候选人是..."}
data: [DONE]
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 503 | `not_ready` | MainAgent 未初始化 |

**处理逻辑**：MainAgent.handle_chat() → LLM 对话 + 工具调用（如 delegate_to_resume_agent、update_user_memory）→ 流式返回

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
  "questions": [ ...InterviewQuestion ]
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 404 | `not_found` | 候选人不存在 |

**处理逻辑**：加载候选人 → 创建/更新 Session → 调用 MainAgent.set_candidate_context()

---

### 简历相关

#### `POST /api/resume/upload`

上传候选人 PDF 简历，保存文件和提取文本，返回 file_path。**不直接触发 LLM 解析**，解析由前端通过聊天框发送消息给 MainAgent 触发。

**请求**：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | File（PDF） | 是 | 简历 PDF 文件 |
| `candidate_id` | string（query） | 否 | 指定已有候选人 ID |
| `overwrite` | bool（query） | 否 | 是否覆盖同名候选人 |

**响应** `200 OK`：

```json
{
  "file_path": "/absolute/path/to/resume.pdf",
  "session_id": "uuid",
  "candidate_id": "uuid"
}
```

**错误码**：

| HTTP | code | 说明 |
|---|---|---|
| 400 | `invalid_file_type` | 非 PDF 格式 |
| 409 | `duplicate_candidate` | 同名候选人已存在 |

---

#### `GET /api/resume/profile`

获取候选人画像和题目清单。

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

---

### 题目管理

#### `GET /api/interview/questions`

获取当前会话的题目清单。

**请求参数**（query）：`candidate_id` (string, 必填)

**响应** `200 OK`：`{"questions": [...]}`

---

#### `PUT /api/interview/questions`

更新当前会话的题目清单。

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

**处理逻辑**：InterviewController.start_interview() → 启动 AudioManager → 绑定 WS 广播

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

**处理逻辑**：InterviewController.stop_interview() → flush_pending_round() → 停止 AudioManager

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
    "dimensions": [...],
    "overall_score": 7.5,
    "strengths": [...],
    "weaknesses": [...],
    "recommendation": "hire",
    "summary": "..."
  }
}
```

**处理逻辑**：EvalAgent.handle_request("generate_eval") → save_eval_report → close_session

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
| `keyword` | string | `""` | 按姓名模糊搜索 |
| `limit` | int | 20 | 每页条数 |
| `offset` | int | 0 | 偏移量 |

---

#### `GET /api/candidates/{candidate_id}/history`

获取候选人历史面试记录。

---

#### `DELETE /api/candidates/{candidate_id}`

删除候选人（级联删除简历、面试记录、评价报告）。

---

#### `GET /api/recordings/{session_id}/rounds/{round_number}`

下载指定轮次的录音文件（WAV）。

---

## WebSocket `/ws/interview`

连接地址：`ws://HOST:PORT/ws/interview`

### 服务端推送消息

| 类型 | 说明 |
|---|---|
| `session_snapshot` | 会话状态快照 |
| `transcript` | 实时转写片段（source + text + is_final） |
| `suggestion_delta` | 追问建议流式片段 |
| `suggestion_final` | 追问建议完成标志 |
| `status` | 操作状态通知 |
| `error` | 错误通知 |
| `heartbeat` | 心跳响应 |

### 客户端发送消息

| 类型 | 说明 |
|---|---|
| `manual_input` | 手动输入文字（source + text） |
| `request_suggestion` | 手动触发追问建议 |
| `set_trigger_mode` | 切换追问触发模式（auto/manual） |
| `switch_agent` | 兼容消息，映射到 start_interview / stop_interview |
| `heartbeat` | 保活 ping |

---

## 接口与 Agent 操作对应关系

| 接口 | 处理逻辑 |
|---|---|
| `POST /api/chat` | MainAgent.handle_chat() → LLM + 工具调用 |
| `POST /api/candidate/select` | MainAgent.set_candidate_context() |
| `POST /api/resume/upload` | 保存文件，返回 file_path（解析由聊天触发） |
| `GET /api/resume/profile` | 读 DB + session 缓存 |
| `POST /api/interview/start` | InterviewController.start_interview() → AudioManager.start() |
| `POST /api/interview/stop` | InterviewController.stop_interview() → AudioManager.stop() |
| `POST /api/session/switch` | 兼容接口：interview → start_interview()，eval → stop_interview() |
| `POST /api/interview/suggest` | InterviewController.interview_agent.handle_request("trigger_suggestion") |
| `GET /api/interview/eval` | InterviewController.eval_agent.handle_request("generate_eval") → close_session() |
| WS `manual_input` | TranscriptionManager.on_segment() → SuggestionTrigger |
| WS `request_suggestion` | InterviewController.interview_agent.handle_request("trigger_suggestion") |
| WS `set_trigger_mode` | InterviewController.interview_agent.handle_request("set_trigger_mode") |
| WS `switch_agent` | 兼容消息：interview → start_interview()，eval → stop_interview() |
