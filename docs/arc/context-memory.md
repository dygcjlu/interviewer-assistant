# 上下文与记忆管理

本文档说明系统的三层记忆体系：面试实时上下文（ContextManager）、面试官全局记忆（UserMemoryStore + USER.md）、候选人长期记忆（MemoryModule + 文件系统）。

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

| 参数 | 默认值 | 环境变量 | 含义 |
|---|---|---|---|
| `window_size` | 6 | `CONTEXT_WINDOW_SIZE` | 滑动窗口保留的最新轮次数 |
| `token_budget` | 80000 | `CONTEXT_TOKEN_BUDGET` | token 预算上限 |
| `token_safety_margin` | 0.2 | — | 安全余量系数（实际可用 = budget × 0.8） |
| `compression_round_threshold` | 8 | `CONTEXT_COMPRESSION_THRESHOLD` | 超过多少轮次时触发压缩 |
| `model_context_limit` | 32000 | — | 用于压缩可行性检查的模型上限 |

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

在发送给 LLM 压缩之前，用 `llm_client.count_tokens()` 精确估算压缩请求的 token 数（系统提示 + 对话文本拼成虚拟消息列表，整体调用一次）：

```python
estimated_tokens = self._llm_client.count_tokens([
    Message(role="system", content=_COMPRESSION_SYSTEM_PROMPT),
    Message(role="user", content=conversation_text),
])
```

若估算超过 `model_context_limit × 0.7`（即 22400），则强制只保留 head 的第 1 轮，防止压缩请求本身超过上下文窗口。

Phase 2 的 tail 边界截断同样使用 `count_tokens`：从后往前扩大 tail 候选窗口时，对整份虚拟消息列表整体计数（而非逐轮 `len//3` 累加）。

最终以独立 LLM 调用生成摘要：

```python
messages = [
    system: "请将以下面试对话轮次压缩为简洁的结构化摘要，保留候选人的技术亮点、短板和关键回答要点。",
    user:   "{conversation_text}",
]
response = await llm_client.chat(messages, temperature=0.3)
self._summary = "[以下为早期面试对话的压缩摘要，非原始记录]\n" + response.content
```

压缩完成后，`_all_rounds` 截断为只保留最新 `window_size` 轮。同时经 `set_compress_done_handler` 注册的回调将摘要同步到 `session.context_summary`（供 `finish_interview` 持久化）。

### 1.5 Token 预算监控

`token_usage` 属性通过 `llm_client.count_tokens()` 精确估算当前 prompt 的 token 消耗，供监控使用（未阻断流程）：

- **固定区**：占位 system 文本单独计数
- **摘要区**：`_summary` 作为一条 system 消息计数
- **窗口区**：每轮 `interviewer_text + candidate_text` 拼成一条 user 消息计数

`_estimate_tokens()` 则将固定区 + 摘要 + 全部轮次拼成**一份虚拟消息列表**，整体调用一次 `count_tokens()`（避免逐段分别计数导致 overhead 与安全余量重复叠加）。

```
utilization = total_used / (token_budget × (1 - token_safety_margin))
```

### 1.6 生命周期

| 事件 | 操作 |
|---|---|
| 新面试会话开始 | `reset()` 清空所有轮次、摘要，取消进行中的压缩任务 |
| 面试进行中 | 每轮结束后 `add_round()` 追加轮次 |
| 维度更新 | `update_covered_dimensions()` 同步已覆盖维度 |
| Agent 构建 prompt | `get_context()` 快速返回当前数据 |
| 压缩完成 | `set_compress_done_handler` 回调更新 `session.context_summary` |

---

## 二、面试官全局记忆（UserMemoryStore + USER.md）

**文件**：`src/storage/user_memory.py`，`USER.md`（项目根目录）

USER.md 是面试官的**持久化偏好文件**，是跨服务重启的唯一记忆载体。由 `UserMemoryStore` 管理，支持条目级精确操作。

### 2.1 条目化存储格式

USER.md 内容以 `ENTRY_DELIMITER`（`\n\n---\n\n`）分隔多个独立条目，每个条目是一段自由格式文本：

```markdown
招聘后端工程师，要求 3 年以上 Go 或 Python 经验，熟悉分布式系统设计

---

面试风格偏好：重点考察候选人的系统思维，对项目经历要多追问细节和量化指标
```

**向后兼容**：若 USER.md 不含 `---` 分隔符（旧格式），整个文件作为 `entries[0]` 加载。

### 2.2 UserMemoryStore API

| 方法 | 说明 |
|---|---|
| `load()` | 从磁盘读取并解析条目列表 |
| `render()` | 拼接所有条目为完整文本，供注入 system prompt |
| `list_entries()` | 返回 `[{"index": 0, "content": "..."}]`，供 LLM 选择操作对象 |
| `add(content)` | 追加新条目，返回索引；超出字符上限（默认 3000）时拒绝 |
| `replace(index, content)` | 替换指定索引的条目 |
| `remove(index)` | 删除指定索引的条目 |

**原子写入**：所有写操作通过 `mkstemp + os.replace` 写入临时文件后原子替换，避免写入中途崩溃导致文件损坏。

### 2.3 manage_user_memory 工具

LLM 通过 `manage_user_memory` 工具（`src/tools/manage_user_memory.py`）操作 `UserMemoryStore`：

```json
{
  "action": "list|add|replace|remove",
  "index": "(replace/remove 时必填)",
  "content": "(add/replace 时必填)"
}
```

典型工作流：
```
面试官: "这次招的是 Go 方向，要求有 Kubernetes 运维经验"
    ↓
MainAgent LLM 调用 manage_user_memory(action="list")
    → 返回现有条目列表
    ↓
若已有相似条目：manage_user_memory(action="replace", index=0, content="Go 方向…")
若无相似条目：  manage_user_memory(action="add", content="Go 方向…")
    ↓
工具执行：调用 store.replace()/store.add()，原子写入 USER.md
    ↓
reload_user_memory()：MainAgent + PromptBuilder 刷新内存中的 _user_memory
    ↓
下一次 _build_system_prompt() / PromptBuilder.build() 时生效
```

### 2.4 在各 Agent 提示词中的位置

| Agent | 注入位置 |
|---|---|
| MainAgent | Layer 2（系统提示 `## 面试官偏好与岗位要求`） |
| InterviewAgent/ResumeAgent | PromptBuilder Layer 5（固定区末尾 `## 面试官岗位要求与偏好`） |
| EvalAgent | 独立 system 消息（`## 岗位要求与面试官偏好`，由 `UserMemoryStore.render()` 直接读取） |

### 2.5 Memory Nudge（后台记忆审查）

MainAgent 内置 nudge 机制，每隔 `_NUDGE_INTERVAL`（默认 10 轮）自动触发一次后台记忆审查：

```
每轮对话结束后：
  _turns_since_nudge += 1
  若 >= 10 → 设 _should_nudge = True

若当前轮 LLM 主动调用了 manage_user_memory → 重置计数器

轮次正常完成且 _should_nudge：
  asyncio.create_task(_background_memory_review())
```

`_background_memory_review()` 流程：
1. 取最近 12 条消息（约 6 轮对话）
2. 发给 LLM，仅暴露 `manage_user_memory` 工具
3. LLM 判断是否有值得保存的信息；若有则调用工具，若无则直接结束
4. 最多迭代 3 次；捕获所有异常，不影响主流程

---

## 三、候选人长期记忆（MemoryModule + 文件系统）

**文件**：`src/storage/memory_module.py`

MemoryModule 统一管理所有需要跨会话持久化的数据，通过文件系统读写实现。

### 3.1 短期记忆 vs 长期记忆

| 维度 | 短期记忆 | 长期记忆 |
|---|---|---|
| 载体 | `InterviewSession` 对象（内存） | 文件系统（`candidates/` 目录） |
| 范围 | 单次面试会话期间 | 跨会话、跨服务重启 |
| 管理方 | `InterviewController`（Agent 层） | `MemoryModule`（Storage 层） |
| 内容 | 实时对话轮次、题目覆盖状态、当前阶段 | 候选人档案、历史面试记录、评价报告 |
| 生命周期 | 随 `create_session()` 创建，`close_session()` 清空 | 永久保存，支持查询和删除 |

### 3.2 候选人历史记忆注入

当候选人有历史面试记录时，该信息会通过 PromptBuilder Layer 4 注入到所有子 Agent 的 prompt 中。

**加载流程**：

```
InterviewController.create_session(candidate_id)
    ↓
memory.get_candidate_history(candidate_id, limit=3)
    → 读取 candidates/{id}/interviews/index.md
    → 取最近 3 次面试条目（含评分、关键结论）
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
   关键发现: 优势: 系统设计能力强; Redis 使用经验丰富，不足: 算法基础偏弱

2. 2025-08-20 14:30 — 综合评分 6.0/10，推荐 weak_hire
   关键发现: ...
```

关键发现（`key_findings`）由 `save_eval_report()` 从评价报告的 `strengths` 和 `weaknesses` 字段自动提取（各取前 2 条）。

### 3.3 文件系统数据持久化

| 操作 | 时机 | 方法 |
|---|---|---|
| 保存候选人档案 | 简历解析完成后（dispatch_to_agent parse_done） | `save_candidate(profile, resume_markdown)` |
| 保存面试简报 | dispatch_to_agent brief_done 时 | `save_brief(candidate_id, content)` |
| 生成问题清单 | brief_done 后异步任务 | `save_questions(candidate_id, questions)` → `questions.json` |
| 面试开始记录 | `InterviewController.start_interview()` | `start_interview(session)`（写 session.json） |
| 轮次 WAL 追加 | 每轮归档后（`on_round_finalized`） | `append_round(candidate_id, interview_id, round)` → `rounds.jsonl` |
| 保存面试记录 | `close_session()` 时 | `finish_interview(session)`（写 transcript.md + 归档 WAL + 更新 index） |
| 保存评价报告 | EvalAgent 生成后 | `save_eval_report(report)` |

---

## 四、rounds.jsonl WAL 与崩溃恢复

**文件**：`src/storage/memory_module.py`

面试过程中，每次 `finalize_round()` 归档时，`append_round()` 将对话轮次以 JSONL 格式追加写入 `candidates/{id}/interviews/{interview_id}/rounds.jsonl`（Write-Ahead Log）。

`finish_interview()` 完成后 WAL 归档：轮次数据写入 `transcript.md`，WAL 文件重命名为 `rounds.jsonl.archived`。

**崩溃恢复场景**：若进程在 `finish_interview()` 之前异常退出，WAL 文件保留在原路径（`rounds.jsonl`，无 `.archived` 后缀），可通过 Recovery API 恢复：

| 步骤 | 操作 | API |
|---|---|---|
| 1. 扫描 | `scan_orphan_wal()` 找出所有未归档的 `rounds.jsonl` | `GET /api/recovery/scan` |
| 2. 恢复 | `recover_interview_from_wal()` 重建 rounds → 写 `transcript.md` → 归档 WAL | `POST /api/recovery/finish` |
| 3. 丢弃 | `discard_orphan_wal()` 删除不需要恢复的 WAL | `POST /api/recovery/discard` |

启动时 `lifespan` 也会调用 `scan_orphan_wal()`，将残留提示写入 `startup_warnings` 供 UI 横幅展示。

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

## 六、ConversationLogger（对话持久化）

**文件**：`src/storage/conversation_logger.py`

ConversationLogger 将 Agent 与 LLM 之间的完整消息列表以 JSONL 格式持久化到本地文件，用于调试和事后审查。它**不影响运行时逻辑**，写入通过 `asyncio.to_thread` 在后台完成，不阻塞事件循环。

### 6.1 文件路径

| Agent | 文件路径 |
|---|---|
| MainAgent | `conversations/main_agent.jsonl` |
| InterviewAgent | `conversations/interview_agent_{session_id}.jsonl` |

`conversations/` 目录已加入 `.gitignore`，不提交到版本库。

### 6.2 JSONL 格式

每行一个 JSON 对象：
```json
{"role": "system", "content": "你是一位专业的技术面试助手...", "timestamp": "2026-05-20T12:00:00"}
{"role": "user",   "content": "请解析简历 resumes/张三.pdf", "timestamp": "2026-05-20T12:00:01"}
{"role": "assistant", "tool_calls": [...], "timestamp": "2026-05-20T12:00:02"}
{"role": "tool", "tool_call_id": "call_xxx", "content": "...", "timestamp": "2026-05-20T12:00:03"}
```

### 6.3 两种写入模式

| 方法 | 用途 |
|---|---|
| `append(messages)` | 追加一组消息（InterviewAgent 每轮追问后调用） |
| `append_with_system(system_content, messages)` | 若 system prompt 与上次不同，先写入 system 行，再写其余消息（MainAgent 每轮对话后调用，自动去重 system 行） |

### 6.4 生命周期

- **MainAgent**：`ConversationLogger` 在 `__init__` 时创建（单例），随服务存活；`append_with_system` 在每次 `handle_chat()` 完成后调用
- **InterviewAgent**：`ConversationLogger` 在 `on_activate(session)` 时创建（会话级），写入 `conversations/interview_agent_{session_id}.jsonl`；每次 `generate_suggestion()` 完成后调用 `append`

---

## 七、三层记忆体系全景

```
┌─────────────────────────────────────────────────────────────────────┐
│                         记忆体系全景                                 │
├─────────────┬───────────────────────────────────────────────────────┤
│ 层级        │ 内容                                                   │
├─────────────┼───────────────────────────────────────────────────────┤
│ 面试官记忆  │ UserMemoryStore / USER.md：条目化存储岗位要求、面试风格 │
│             │ 工具：manage_user_memory (add/replace/remove/list)      │
│             │ 后台审查：MainAgent memory nudge（每 10 轮）            │
├─────────────┼───────────────────────────────────────────────────────┤
│ 候选人长期  │ 文件系统（candidates/ 目录）：历史面试记录、评价报告    │
│ 记忆        │ → 注入到 PromptBuilder Layer 4（history_summary）       │
├─────────────┼───────────────────────────────────────────────────────┤
│ 本次面试    │ InterviewSession（内存）：面试简报、阶段状态              │
│ 固定信息    │ candidate.resume_content：简历 Markdown 正文            │
│             │ interview_brief：面试简报 Markdown（brief.md）          │
│             │ → 注入到 PromptBuilder Layer 5（fixed zone）            │
├─────────────┼───────────────────────────────────────────────────────┤
│ 本次面试    │ ContextManager：_summary（LLM 压缩摘要）                │
│ 历史上下文  │ → 注入到 PromptBuilder Layer 6                         │
│             ├───────────────────────────────────────────────────────┤
│             │ ContextManager：_all_rounds 原文轮次                    │
│             │ → Layer 7：ResumeAgent 滑动窗口（默认 6 轮）            │
│             │           InterviewAgent 全量轮次（full_history=True）  │
├─────────────┼───────────────────────────────────────────────────────┤
│ 调试持久化  │ ConversationLogger：Agent ↔ LLM 完整消息 JSONL          │
│             │ conversations/main_agent.jsonl                         │
│             │ conversations/interview_agent_{session_id}.jsonl       │
└─────────────┴───────────────────────────────────────────────────────┘
```
