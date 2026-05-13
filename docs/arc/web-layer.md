# Web 层

## 1. 后端 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/resume/upload` | POST | 上传简历 PDF |
| `/api/resume/profile` | GET | 获取候选人画像 |
| `/api/interview/questions` | GET/PUT | 获取/修改题目清单 |
| `/api/interview/start` | POST | 开始面试（启动音频 + STT + 录音） |
| `/api/interview/stop` | POST | 结束面试（停止录音、触发评价） |
| `/api/session/switch` | POST | 切换活跃 Agent（需满足前置条件） |
| `/api/interview/suggest` | POST | 手动触发建议生成 |
| `/api/interview/eval` | GET | 获取评价报告 |
| `/api/candidates` | GET | 候选人列表/搜索 |
| `/api/candidates/{id}/history` | GET | 历史面试记录 |
| `/api/recordings/{session_id}/rounds/{round}` | GET | 获取某轮次音频切片 |
| `/api/session/current` | GET | 获取当前会话完整状态（前端重连/刷新时使用） |
| `/ws/interview` | WebSocket | 实时推送转写 + 建议 |

### 1.2 REST API 请求/响应定义

> 数据类型定义见 [共享数据结构](./data-models.md)。错误响应格式见 [配置与运维](./config-and-ops.md) 3.2 节。

#### POST /api/resume/upload

上传并解析简历，自动创建候选人画像、生成面试题目。

- 请求：`Content-Type: multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | File | 是 | 简历 PDF 文件 |
| `candidate_id` | string | 否 | 已有候选人 ID（更新简历场景） |

- 响应：`200 OK`

```json
{
    "candidate_id": "c-uuid-xxx",
    "profile": { "/* CandidateProfile */": "..." },
    "questions": [
        {
            "id": 1, "dimension": "系统设计",
            "question": "请描述你设计的分布式缓存方案",
            "follow_ups": ["缓存一致性如何保证？"],
            "difficulty": "medium", "source": "auto", "is_covered": false
        }
    ]
}
```

#### GET /api/resume/profile

| 查询参数 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| `candidate_id` | string | 是 | 候选人 ID |

- 响应：`200 OK` — 同 `POST /api/resume/upload` 响应格式

#### GET /api/interview/questions

| 查询参数 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| `candidate_id` | string | 是 | 候选人 ID |

- 响应：`200 OK`

```json
{
    "questions": [ "/* InterviewQuestion[] */" ]
}
```

#### PUT /api/interview/questions

面试官手动调整题目清单（增删改排序）。

- 请求：

```json
{
    "candidate_id": "c-uuid-xxx",
    "questions": [ "/* InterviewQuestion[] — 完整替换 */" ]
}
```

- 响应：`200 OK` — 同 GET 响应格式（返回更新后的完整列表）

#### POST /api/interview/start

开始面试：创建会话 → 切换到 InterviewAgent → 启动音频/STT/录音。

- 请求：

```json
{
    "candidate_id": "c-uuid-xxx",
    "trigger_mode": "auto"
}
```

- 响应：`200 OK`

```json
{
    "session_id": "s-uuid-xxx",
    "stage": "interviewing"
}
```

#### POST /api/interview/stop

结束面试：停止录音 → 切换到 EvalAgent。

- 请求：无 body（操作当前活跃 session）
- 响应：`200 OK`

```json
{
    "session_id": "s-uuid-xxx",
    "stage": "evaluating",
    "total_rounds": 12,
    "total_duration_sec": 2400.5
}
```

#### POST /api/session/switch

切换活跃 Agent（请求格式已在 agent-orchestrator.md 中定义）。

- 响应：`200 OK`

```json
{
    "stage": "interviewing",
    "active_agent": "interview"
}
```

#### POST /api/interview/suggest

手动触发建议生成。建议内容通过 WebSocket `suggestion` 消息流式推送，此接口仅负责触发。

- 请求：无 body
- 响应：`200 OK`

```json
{
    "request_id": 5,
    "status": "generating"
}
```

#### GET /api/interview/eval

| 查询参数 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| `interview_id` | string | 否 | 面试 ID，不传则使用当前活跃 session |

- 响应：`200 OK`

```json
{
    "report": {
        "id": "e-uuid-xxx",
        "interview_id": "s-uuid-xxx",
        "dimensions": [
            { "dimension": "系统设计", "score": 7.5, "comment": "...", "evidence": ["..."] }
        ],
        "overall_score": 7.0,
        "strengths": ["分布式系统经验丰富"],
        "weaknesses": ["算法基础偏弱"],
        "recommendation": "hire",
        "summary": "..."
    }
}
```

#### GET /api/candidates

| 查询参数 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| `keyword` | string | 否 | 按姓名模糊搜索 |
| `limit` | int | 否 | 返回数量上限，默认 20 |
| `offset` | int | 否 | 偏移量，分页用 |

- 响应：`200 OK`

```json
{
    "candidates": [
        {
            "id": "c-uuid-xxx",
            "name": "张三",
            "last_interview_date": "2026-05-10T14:00:00",
            "interview_count": 2
        }
    ],
    "total": 15
}
```

#### GET /api/candidates/{id}/history

- 响应：`200 OK`

```json
{
    "candidate": { "/* CandidateProfile */": "..." },
    "interviews": [
        {
            "interview_id": "s-uuid-xxx",
            "date": "2026-05-10T14:00:00",
            "overall_score": 7.0,
            "recommendation": "hire",
            "total_rounds": 12
        }
    ]
}
```

#### GET /api/recordings/{session_id}/rounds/{round}

| 查询参数 | 类型 | 必填 | 说明 |
|----------|------|------|------|
| `source` | string | 否 | `"candidate"` \| `"interviewer"`，不传返回混合音频 |

- 响应：`audio/wav` 文件流

#### GET /api/session/current

获取当前会话完整状态（前端刷新/重连时同步用）。

- 响应：`200 OK`

```json
{
    "session": {
        "id": "s-uuid-xxx",
        "stage": "interviewing",
        "active_agent": "interview",
        "candidate_name": "张三",
        "trigger_mode": "auto",
        "rounds_count": 5,
        "token_used": 12000,
        "token_budget": 80000
    }
}
```

无活跃会话时：`{ "session": null }`

---

## 2. WebSocket 消息协议

### 2.1 下行消息（后端 → 前端）

```jsonc
// 实时转写
{
    "type": "transcript",
    "source": "candidate",       // "candidate" | "interviewer"
    "text": "我在上一个项目中使用了 Redis 做分布式缓存...",
    "is_final": true
}

// 追问建议（流式，逐 token 推送）
{
    "type": "suggestion",
    "request_id": 3,             // 递增 ID，前端以最新为准
    "delta": "Redis 集群",       // 本次增量文本片段（流式中间态）
    "text": "可以追问：Redis 集群的数据分片策略是怎样的？",  // 累积完整文本（仅 is_final=true 时有意义）
    "is_final": false            // false=流式中间片段, true=生成完毕
}
// 前端处理逻辑：
// - is_final=false 时：将 delta 追加到当前建议文本末尾，实现打字机效果
// - is_final=true 时：以 text 字段为最终完整内容
// - 收到新 request_id 时：清空旧建议内容，开始渲染新建议

// 状态变更
{
    "type": "status",
    "stage": "interviewing",
    "message": "面试进行中"
}

// Token 用量
{
    "type": "token_usage",
    "used": 12000,
    "budget": 80000
}

// 错误通知
{
    "type": "error",
    "code": "stt_disconnected",    // 错误码，前端据此决定展示方式
    "message": "语音识别连接中断，正在重连...",
    "recoverable": true            // true=自动恢复中, false=需要用户干预
}

// 会话状态快照（用于前端连接/重连时同步状态）
{
    "type": "session_snapshot",
    "session_id": "abc123",
    "stage": "interviewing",
    "trigger_mode": "auto",
    "rounds_count": 5,
    "token_used": 12000,
    "token_budget": 80000
}
```

### 2.2 上行消息（前端 → 后端）

```jsonc
// 手动触发建议生成
{
    "type": "request_suggestion"
}

// 手动输入文字（STT 降级时使用）
{
    "type": "manual_input",
    "source": "interviewer",
    "text": "请详细说一下你做的分布式缓存方案"
}

// 切换建议触发模式
{
    "type": "set_trigger_mode",
    "mode": "auto"               // "auto" | "manual"
}

// 切换活跃 Agent
{
    "type": "switch_agent",
    "target_agent": "interview"  // "resume" | "interview" | "eval"
}
```

---

## 3. Vue 3 前端

核心页面：

1. **首页/候选人列表** — 历史面试记录检索，按姓名/时间筛选
2. **面试准备页** — 上传简历、查看候选人画像、审阅/调整题目清单
3. **面试控制台** — 左侧主区域实时转写记录，右侧栏追问建议（流式打字机效果逐 token 渲染，新建议到达时替换旧内容），底部控制栏（开始/结束/触发模式切换/Token 用量）
4. **评价报告页** — 结构化评分 + 文字报告 + 面试记录回放（可按轮次播放音频、对照转写文本）

---

## 4. 设计决策

### 决策 2: Web 框架

```
├── 方案 A: FastAPI（原生 async、WebSocket 支持好、自带 OpenAPI 文档）
├── 方案 B: aiohttp（更底层，灵活度高）
└── 选择: FastAPI
    理由: 开发效率高，生态好，与 asyncio 原生配合，自带 API 文档。
```

### 决策 3: 实时推送协议

```
├── 方案 A: WebSocket（双向通信，低延迟）
├── 方案 B: SSE（单向推送，简单）
└── 选择: WebSocket
    理由: 前端也需要发送命令（手动触发建议、切换模式、手动输入），双向通信更合适。
```

### 决策 4: 前端部署方式

```
├── 方案 A: 开发期独立 dev server + 代理，生产打包后 FastAPI 静态文件服务
├── 方案 B: 完全分离的前后端
└── 选择: 方案 A
    理由: 本地单机部署，最终打包为一个服务最简单，一条命令启动。
```
