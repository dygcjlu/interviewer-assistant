# 上下文与记忆管理

本文档说明系统的三层记忆体系：面试实时上下文（ContextManager）、面试官全局记忆（USER.md）、候选人长期记忆（MemoryModule + SQLite）。

---

## 一、面试对话上下文（ContextManager）

**文件**：`src/framework/context.py`

ContextManager 负责管理面试进行中的对话轮次，解决"上下文窗口有限但面试对话可能很长"的问题。核心策略是**滑动窗口 + 后台异步 LLM 压缩**。

### 1.1 数据结构

```python
_all_rounds: list[ConversationRound]  # 全部对话轮次（内存）
_summary:    str                       # 早期轮次的 LLM 压缩摘要
```

每次 `add_round()` 往 `_all_rounds` 追加一条 `ConversationRound`：

| 字段 | 说明 |
|---|---|
| `round_number` | 轮次编号 |
| `interviewer_text` | 面试官本轮发言 |
| `candidate_text` | 候选人本轮回答 |
| `llm_suggestion` | AI 生成的追问建议（可为空） |
| `timestamp` | 时间戳 |

### 1.2 上下文配置（ContextConfig）

| 参数 | 默认值 | 含义 |
|---|---|---|
| `window_size` | 6 | 滑动窗口保留的最新轮次数 |
| `token_budget` | 80000 | token 预算上限（来自 `CONTEXT_TOKEN_BUDGET` 环境变量） |
| `token_safety_margin` | 0.2 | 安全余量系数（实际可用 = budget × 0.8） |
| `compression_round_threshold` | 8 | 超过多少轮次时触发压缩 |
| `model_context_limit` | 32000 | 用于压缩可行性检查的模型上限 |

### 1.3 滑动窗口

`get_context()` 每次调用时，取 `_all_rounds[-window_size:]` 作为当前窗口，始终快速返回，不等待压缩完成：

```
压缩前：
_all_rounds = [R1, R2, R3, R4, R5, R6, R7, R8]
window      = [         R3, R4, R5, R6, R7, R8]  ← 最新 6 轮

压缩后：
_all_rounds = [                  R3, R4, R5, R6, R7, R8]  ← 窗口外已压缩
_summary    = "早期 R1/R2 的摘要"
```

注入到 PromptBuilder 时：
- Layer 6 = `_summary`（压缩摘要，若有）
- Layer 7 = 窗口内最新 6 轮的完整内容

### 1.4 异步三阶段压缩

**触发条件**：`len(_all_rounds) > compression_round_threshold`（默认 8 轮）且当前没有压缩任务进行中

**执行方式**：`asyncio.create_task(_compress_async())`，不阻塞主流程

#### Phase 1：剪枝（Pruning）

对窗口外的早期轮次，去除 `llm_suggestion` 字段（追问建议属于低价值内容，不需要进摘要），只保留 `interviewer_text` 和 `candidate_text`。

#### Phase 2：head/tail 截断

若剪枝后的轮次仍超过 `_HEAD + _TAIL`（2 + 3 = 5 轮），只保留头 2 轮和尾 3 轮，丢弃中间轮次：

```
[R1, R2, R3, R4, R5, R6, R7]
     ↓ 截断
[R1, R2,         R5, R6, R7]  ← 保留首尾
```

目的是尽量覆盖"面试开场"和"最近几轮"，避免中间冗余内容消耗 token。

#### Phase 3：可行性检查 + LLM 摘要

在发送给 LLM 压缩之前，估算请求的 token 数：

```python
estimated_tokens = len(conversation_text) / 1.5 + 2000
```

若估算超过 `model_context_limit × 0.7`（即 22400），则强制只保留 head 的第 1 轮，防止压缩请求本身超过上下文窗口。

最终以独立 LLM 调用生成摘要：

```python
messages = [
    system: "请将以下面试对话轮次压缩为简洁的结构化摘要，保留候选人的技术亮点、短板和关键回答要点。",
    user:   "{conversation_text}",
]
response = await llm_client.chat(messages, temperature=0.3)
self._summary = "[以下为早期面试对话的压缩摘要，非原始记录]\n" + response.content
```

压缩完成后，`_all_rounds` 截断为只保留最新 `window_size` 轮。

### 1.5 Token 预算监控

`token_usage` 属性实时估算当前 prompt 的 token 消耗，供监控使用（未阻断流程）：

```
total = fixed_zone(1500) + summary(len/3) + window_rounds(sum(len/3))
utilization = total / (token_budget × 0.8)
```

### 1.6 生命周期

| 事件 | 操作 |
|---|---|
| 新面试会话开始 | `reset()` 清空所有轮次、摘要、维度，取消进行中的压缩任务 |
| 面试进行中 | 每轮结束后 `add_round()` 追加轮次 |
| 维度更新 | `update_covered_dimensions()` 同步已覆盖维度 |
| Agent 构建 prompt | `get_context()` 快速返回当前数据 |

---

## 二、面试官全局记忆（USER.md）

**文件**：`USER.md`（项目根目录），`src/agents/main_agent.py`

USER.md 是面试官的**持久化偏好文件**，是跨服务重启的唯一记忆载体。

### 2.1 内容

面试官在与 MainAgent 对话时提供的任何岗位要求、技术偏好、面试风格等信息都会被追加写入，例如：

```markdown
# 岗位要求

招聘后端工程师，要求 3 年以上 Go 或 Python 经验，熟悉分布式系统设计...

## 面试风格偏好

- 重点考察候选人的系统思维而非算法题
- 对项目经历要多追问细节和量化指标
```

### 2.2 读写机制

**读取（两个时机）**：
1. 服务启动时 `MainAgent.__init__()` → `_load_user_memory()` 读入 `_layer2_user_memory`
2. 工具调用更新后 → `reload_user_memory()` 重新读取，即时生效

**写入（工具调用）**：
```
面试官: "我们这次招的是 Go 方向，要求有 Kubernetes 运维经验"
    ↓
MainAgent LLM 决定调用 update_user_memory(content="Go 方向，要求 K8s 运维经验")
    ↓
工具执行：追加写入 USER.md
    ↓
reload_user_memory()：刷新 _layer2_user_memory
    ↓
下一次 _build_system_prompt() 时生效
```

### 2.3 在 MainAgent 提示词中的位置

USER.md 内容作为 MainAgent 系统提示的第 2 层：

```
## 面试官偏好与岗位要求

{USER.md 全文}
```

---

## 三、候选人长期记忆（MemoryModule + SQLite）

**文件**：`src/storage/memory_module.py`

MemoryModule 统一管理所有需要跨会话持久化的数据，通过 SQLite + aiosqlite 异步读写。

### 3.1 短期记忆 vs 长期记忆

| 维度 | 短期记忆 | 长期记忆 |
|---|---|---|
| 载体 | `InterviewSession` 对象（内存） | SQLite 数据库 |
| 范围 | 单次面试会话期间 | 跨会话、跨服务重启 |
| 管理方 | `InterviewController`（Agent 层） | `MemoryModule`（Storage 层） |
| 内容 | 实时对话轮次、题目覆盖状态、当前阶段 | 候选人档案、历史面试记录、评价报告 |
| 生命周期 | 随 `create_session()` 创建，`close_session()` 清空 | 永久保存，支持查询和删除 |

### 3.2 候选人历史记忆注入

当候选人有历史面试记录时，该信息会通过 PromptBuilder Layer 4 注入到所有子 Agent 的 prompt 中。

**加载流程**：

```
API 层创建新面试会话
    ↓
MemoryModule.get_candidate_history(candidate_id, limit=3)
    → 查询该候选人最近 3 次面试记录
    → 查询每次面试的 EvalReport
    → _format_history_summary() 格式化为文字
    ↓
session.candidate.history_summary = "候选人 张三 历史面试记录：\n1. ..."
    ↓
PromptBuilder.build() 时注入到 Layer 4
```

**格式**（`_format_history_summary()`）：
```
候选人 张三 历史面试记录：

1. 2026-01-15 10:00 — 综合评分 7.5/10，推荐 hire
   关键发现: 系统设计能力强，Redis 使用经验丰富，算法基础偏弱

2. 2025-08-20 14:30 — 综合评分 6.0/10，推荐 weak_hire
   关键发现: ...
```

### 3.3 面试后记忆整合（consolidate_memory）

面试结束 → EvalAgent 生成评价报告 → **异步**触发 `consolidate_memory(session)`：

```python
# EvalAgent._generate_eval() 末尾
self._consolidate_task = asyncio.get_running_loop().create_task(
    self._memory_module.consolidate_memory(session)
)
```

`consolidate_memory()` 的工作：
1. 查询本次面试的 `EvalReport`
2. 将评价洞察写入候选人 `profile_json` 的 `last_interview_insights` 字段：

```json
{
  "last_interview_insights": {
    "interview_id": "...",
    "generated_at": "2026-05-20T10:30:00",
    "overall_score": 7.5,
    "recommendation": "hire",
    "strengths": ["系统设计思维清晰", "Redis 使用经验丰富"],
    "weaknesses": ["算法基础偏弱"],
    "dimension_scores": {
      "技术深度": 8.0,
      "系统设计": 7.5,
      "项目经验": 7.0
    }
  }
}
```

此整合不阻塞评价报告的返回，在后台异步完成。下次该候选人面试时，上述数据会作为 `history_summary` 的信息来源之一被加载。

### 3.4 SQLite 数据持久化

| 操作 | 时机 | 方法 |
|---|---|---|
| 保存候选人档案 | 简历解析完成后 | `save_candidate(profile)` |
| 保存面试记录 | `close_session()` 时 | `save_interview(session)` |
| 保存评价报告 | EvalAgent 生成后 | `save_eval_report(report)` |
| 整合历史洞察 | 评价保存后异步 | `consolidate_memory(session)` |

---

## 四、InterviewAgent 的追问对话历史

除了 ContextManager 管理的面试对话轮次外，InterviewAgent 还维护了一份**自己的追问对话历史** `self._history`，记录 AI 追问建议的生成过程。

**与 ContextManager 的区别**：

| 维度 | ContextManager | InterviewAgent._history |
|---|---|---|
| 内容 | 面试官 ↔ 候选人的对话（转写文本） | AI 追问建议的 user/assistant 消息 |
| 用途 | 注入 PromptBuilder Layer 6/7 | 追加在 PromptBuilder 七层之后，为 LLM 提供追问上下文 |
| 上限 | 配置的 window_size（默认 6 轮） | 20 条消息（约 10 轮追问） |
| 压缩 | 有（三阶段 LLM 压缩） | 无（超出直接截断） |
| 生命周期 | 整个面试会话 | `on_activate()` 初始化，`on_deactivate()` 清空 |

**取消时的数据一致性保护**：当流式生成被取消（候选人继续说话导致旧建议作废）时，`generate_suggestion()` 会将已追加但未完成的 `user_msg` 从 `_history` 中撤销，避免污染历史。

---

## 五、MainAgent 的对话历史

**文件**：`src/agents/main_agent.py`

MainAgent 维护 `self._history: list[Message]`，记录与面试官的完整对话。

| 参数 | 值 |
|---|---|
| 上限 | 24 条消息（`_HISTORY_LIMIT = 24`） |
| 截断策略 | 超出后保留最新 24 条（`self._history[-24:]`） |
| 持久化 | 不持久化（服务重启后清空） |
| 切换候选人 | 历史不清空，只替换系统提示 Layer 3 |

每次 `handle_chat()` 组装 messages 时：
```python
messages = [Message(role="system", content=system_prompt)]
messages.extend(self._history)  # 完整对话历史
```

---

## 六、三层记忆体系全景

```
┌─────────────────────────────────────────────────────────────────────┐
│                         记忆体系全景                                 │
├─────────────┬───────────────────────────────────────────────────────┤
│ 层级        │ 内容                                                   │
├─────────────┼───────────────────────────────────────────────────────┤
│ 面试官记忆  │ USER.md：岗位要求、面试风格（文件持久化，跨重启）      │
├─────────────┼───────────────────────────────────────────────────────┤
│ 候选人长期  │ SQLite：历史面试记录、评价报告、洞察摘要                │
│ 记忆        │ → 注入到 PromptBuilder Layer 4（history_summary）      │
├─────────────┼───────────────────────────────────────────────────────┤
│ 本次面试    │ InterviewSession（内存）：题目计划、维度覆盖状态        │
│ 固定信息    │ → 注入到 PromptBuilder Layer 5（fixed zone）           │
├─────────────┼───────────────────────────────────────────────────────┤
│ 本次面试    │ ContextManager：_summary（LLM 压缩摘要）               │
│ 历史上下文  │ → 注入到 PromptBuilder Layer 6                        │
│             ├───────────────────────────────────────────────────────┤
│             │ ContextManager：_all_rounds 滑动窗口（最新 6 轮原文）  │
│             │ → 注入到 PromptBuilder Layer 7                        │
├─────────────┼───────────────────────────────────────────────────────┤
│ 追问上下文  │ InterviewAgent._history：最近 10 轮追问建议记录        │
│             │ → 追加在 PromptBuilder 七层之后                       │
└─────────────┴───────────────────────────────────────────────────────┘
```
