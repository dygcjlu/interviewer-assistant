# 提示词动态组装机制

本文档说明系统中各 Agent 如何在运行时动态组装提示词，包括每层的内容、来源和更新时机。

---

## 总览：两套提示词体系

系统中存在两套并行的提示词组装逻辑，分别服务于不同的 Agent：

| Agent | 提示词体系 | 文件 |
|---|---|---|
| **MainAgent** | 自管理三层系统提示 | `src/agents/main_agent.py` |
| **InterviewAgent / ResumeAgent** | `PromptBuilder` 七层统一构建 | `src/framework/prompt_builder.py` |
| **EvalAgent** | 自建 messages（不使用 PromptBuilder） | `src/agents/eval_agent.py` |

---

## 一、MainAgent 的三层系统提示

MainAgent 是面试官的唯一对话入口，全程常驻，自行管理三个独立的提示词层。每次 `handle_chat()` 调用时，通过 `_build_system_prompt()` 将三层拼接为一条 system message 传给 LLM。

```
_build_system_prompt()
    ├── Layer 1: _LAYER1_ROLE          （固定）
    ├── Layer 2: _layer2_user_memory   （UserMemoryStore.render() 内容）
    └── Layer 3: _layer3_candidate     （当前候选人信息）
```

### Layer 1：角色定义（固定）

**来源**：硬编码常量 `_LAYER1_ROLE`

**内容**：
- Agent 角色身份：专业面试助手
- 能力描述：对话理解、简历解析（通过 `dispatch_to_agent`）、偏好记忆（通过 `manage_user_memory`）
- 对话风格要求：简洁专业
- 工具使用规则：解析简历、生成面试简报必须调用 `dispatch_to_agent(agent="resume", ...)`，简报生成后用简短文字总结
- 引导式对话工作流：简历解析后两阶段（呈现分析 → 收集关注点），最终生成结构化面试简报
- 主动行为指引：检测到岗位要求时自动调用 `manage_user_memory` 工具保存

**更新时机**：永不变更

---

### Layer 2：面试官偏好与岗位要求（USER.md）

**来源**：`UserMemoryStore.render()`（从内存中读取，无磁盘 IO）

**内容**：面试官历次在对话中告知的岗位要求、技术偏好、面试风格等持久化信息（条目间以 `---` 分隔）

**注入格式**：
```
## 面试官偏好与岗位要求

{USER.md 全文}
```

**更新时机**：
1. 服务启动时，`MainAgent.__init__()` 调用 `_load_user_memory()` → `UserMemoryStore.render()` 加载一次
2. 面试官在对话中提供新信息 → MainAgent 调用工具 `manage_user_memory` 写入 USER.md → 立即调用 `reload_user_memory()` 刷新本层（store 已是最新，无需重读磁盘）

---

### Layer 3：当前候选人信息（动态替换）

**来源**：`CandidateProfile` 对象 + 面试简报

**内容**（由 `set_candidate_context()` 构建）：
- 候选人姓名、ID
- 当前职位
- 工作年限
- 技能列表（最多 15 项）
- 简历内容（`profile.resume_content` 前 1500 字，即 profile.md 正文）
- 面试简报预览（`interview_brief` 前 800 字）

**注入格式**：
```
## 当前候选人信息

当前候选人：{name}（ID: {id}）
职位：{current_position}
工作年限：{years} 年
技能：{skill1}, {skill2}, ...
简历内容：
{resume_content[:1500]}
面试简报（前800字）：
{interview_brief[:800]}
```

**更新时机**：
- 前端选中候选人时，API 层调用 `set_candidate_context(profile, interview_brief=brief)` 替换本层
- 切换候选人时只替换本层，**对话历史不清空**（保持上下文连续）
- 调用 `clear_candidate_context()` 清空本层

---

## 二、子 Agent 的七层统一提示词（PromptBuilder）

`InterviewAgent`、`ResumeAgent` 通过 `PromptBuilder.build(session, config)` 构建完整的 `messages` 列表。`EvalAgent` 因评价场景的特殊性（需要全量对话 + 岗位要求注入 + 分块 map-reduce）自行组装 messages，不使用 PromptBuilder。

```python
messages = self.prompt_builder.build(self._session, self.config)
```

构建结果是一个有序的 `list[Message]`，**Layer 1~6 全部拼接为单条 `system` 消息**（各层之间用 `\n\n` 分隔），之后 Layer 7 以 `user/assistant` 消息追加，最后由各 Agent 自行追加本轮任务消息。

### 七层结构总览

```
messages = [
    [0] system — Layer 1~6 合并（\n\n 拼接）：
                   Layer 1: Agent 身份定义
                   Layer 2: 可用技巧索引（可选）
                   Layer 3: 可用工具说明（可选）
                   Layer 4: 候选人历史记忆（可选）
                   Layer 5: 面试固定信息区 + 岗位要求（USER.md，可选）
                   Layer 6: 早期对话压缩摘要（可选）
    [1..N] user/assistant×N — 滑动窗口对话轮次（Layer 7）
    ... Agent 自己追加的用户消息
]
```

---

### Layer 1：Agent 身份（Agent Identity）

**来源**：`AgentConfig.system_prompt`，各 Agent 在 `prompts.py` 中定义

| Agent | system_prompt 常量 |
|---|---|
| ResumeAgent | `RESUME_AGENT_SYSTEM_PROMPT` |
| InterviewAgent | `INTERVIEW_AGENT_SYSTEM_PROMPT` |
| EvalAgent | `EVAL_AGENT_SYSTEM_PROMPT` |

**内容举例（InterviewAgent）**：
```
你是一位专业的技术面试助手，在面试进行过程中实时辅助面试官。
候选人的基本信息、面试简报（含关注点和考察维度）已在系统提示中提供。
## 你的核心任务
结合所有对话记录、候选人简历和面试简报，给出一句追问建议或话题切换引导语。
## 输出格式
直接输出一句中文话术，无需解释，无需 JSON 包装。
```

**更新时机**：固定不变，服务启动时一次性配置

---

### Layer 2：可用技巧索引（Skill Index）

**来源**：`SkillLoader.load_index()` 扫描 `skills/` 目录下的所有 `SKILL.md` frontmatter

**触发条件**：`AgentConfig.skill_names` 非空

**内容**：
```
可用面试技巧：
- deep_dive: 技术深挖策略 [当候选人提到技术名词时使用]
- dimension_switch: 维度切换引导 [当前维度已充分覆盖时使用]
```

**设计说明**：此层只注入技巧的"索引"（名称+简介+触发提示），不注入全文。Agent 需要详情时通过 `skill_view` 工具按需加载，避免 prompt 过大。

**哪些 Agent 使用**：
- ResumeAgent：`skill_names=["question_generation"]`（出题方法论，通过 `skill_view('question_generation')` 按需加载）
- InterviewAgent：未配置 `skill_names`（不使用技巧索引）

---

### Layer 3：可用工具说明（Tool Guidance）

**来源**：`ToolRegistry.get_tool(name)` 按名称查询工具描述

**触发条件**：`AgentConfig.tool_names` 非空

**内容**：
```
可用工具：
- parse_resume_pdf: 读取 PDF 简历并提取文本
- file_read: 读取文件内容
- file_write: 写出文件内容
- skill_view: 查看指定面试技巧的完整内容
```

**设计说明**：此层是对 LLM tool call 机制的文字补充说明。实际工具 schema 通过 `tool_registry.get_schemas()` 以标准 OpenAI tools 格式单独传递。

**各 Agent 工具配置**：

| Agent | tool_names |
|---|---|
| ResumeAgent | `parse_resume_pdf`, `file_read`, `file_write`, `skill_view` |
| InterviewAgent | 无（不使用工具） |
| EvalAgent | 自建 messages，不使用 PromptBuilder |

---

### Layer 4：候选人历史记忆（Candidate Long-term Memory）

**来源**：`session.candidate.history_summary`（由 `InterviewController.create_session()` 从文件加载后注入）

**触发条件**：`history_summary` 非空（候选人有历史面试记录）

**内容**（`_format_history_summary()` 格式化）：
```
候选人 张三 历史面试记录：

1. 2026-01-15 10:00 — 综合评分 7.5/10，推荐 hire
   关键发现: 优势: 系统设计能力强; Redis 使用经验丰富，不足: 算法基础偏弱

2. 2025-08-20 14:30 — 综合评分 6.0/10，推荐 weak_hire
   关键发现: ...
```

**更新时机**：每次创建新面试会话时，由 `InterviewController.create_session()` 从 `interviews/index.md` 加载后注入到 `session.candidate.history_summary`

---

### Layer 5：面试固定信息区（Interview Fixed Zone）

**来源**：`_build_fixed_zone(session, user_memory)` 从 `InterviewSession` 和 PromptBuilder 缓存的 `user_memory` 中提取

**内容**（始终存在）：
```
候选人：张三
当前职位：高级后端工程师
工作年限：5 年
年龄：28
技能：Python, Go, Redis, Kubernetes, MySQL

## 候选人简历

{profile.md 完整正文（resume_content）}

（若 resume_content 为空则显示：）
简历档案：candidates/{id}/profile.md（可调用 file_read 工具查看完整内容）

## 面试简报

{interview_brief 全文（来自 candidates/{id}/brief.md）}

（若 interview_brief 为空则此节不显示）

## 面试官岗位要求与偏好
招聘后端工程师，要求 3 年以上 Go 或 Python 经验...（USER.md 全文，若存在）
```

**字段说明**：
- **简历内容**：`resume_content` 非空时直接展示完整 Markdown 正文；为空时展示文件路径
- **面试简报**：`interview_brief` 非空时展示完整简报（含考察维度、关注点等），由 `brief_done` 副作用写入并注入 session
- **岗位要求**：`user_memory` 由 PromptBuilder 从 `UserMemoryStore` 缓存，非空时追加在本层末尾
- 不再包含题目清单、`○`/`✓` 覆盖标记、`已覆盖维度` 等字段（已由面试简报替代）

**更新时机**：每次 `build()` 调用时实时读取 `session` 对象；USER.md 变更后需调用 `prompt_builder.reload_user_memory()` 刷新

---

### Layer 6：早期对话压缩摘要（Summary Zone）

**来源**：`ContextManager._summary`（由后台异步 LLM 压缩生成）

**触发条件**：`context_data.summary` 非空（已触发过至少一次压缩）

**内容**：
```
[以下为早期面试对话的压缩摘要，非原始记录]
候选人对 Redis 的理解较为扎实，能正确描述 RDB/AOF 机制差异，
但在追问 AOF rewrite 触发时机时出现了知识盲点。
系统设计题表现出色，提出了合理的分库分表方案...
```

**更新时机**：由 `ContextManager` 后台异步压缩完成后更新（详见[上下文与记忆管理](./context-memory.md)）

---

### Layer 7：滑动窗口对话轮次（Sliding Window Rounds）

**来源**：`ContextManager.get_context().window_rounds`（`_all_rounds` 的最后 N 条）

**默认窗口大小**：6 轮（`ContextConfig.window_size = 6`，可通过 `CONTEXT_WINDOW_SIZE` 环境变量配置）

**内容**（每轮展开为 1-2 条消息）：
```
user:       面试官：请解释一下 Redis 的 AOF 持久化机制
            候选人：AOF 是 Append Only File，每次写操作都会追加到文件末尾...
assistant:  [追问建议] AOF rewrite 的触发条件是什么，rewrite 期间新的写入如何处理？
```

**设计说明**：
- 面试官和候选人的发言**合并为一条 `user` 消息**，格式为 `面试官：{text}\n候选人：{text}`
- `llm_suggestion` 字段（上一轮 AI 追问建议）以 `role="assistant"` 注入，形成完整对话链；若该轮无追问建议则只有 1 条消息
- 窗口外的历史已被压缩到 Layer 6，此层只保留最近 N 轮原始内容

---

## 三、各 Agent 追加的用户消息

PromptBuilder 的七层是静态背景，各 Agent 在调用 `build()` 后还会追加本次任务的具体指令：

### ResumeAgent

ResumeAgent 采用 **ReAct 模式**（循环调用工具 + LLM），任务由 `dispatch_to_agent` 注入并富化了 session 上下文：

```
user: 请将 resumes/张三.pdf 解析为 Markdown...

[系统上下文]
- 候选人 ID: uuid
- 姓名: 张三
- 持久化简历（file_read 首选）: candidates/uuid/profile.md
- PDF: resumes/张三.pdf
- 临时 Markdown: resumes/张三.md
- 简报路径: candidates/uuid/brief.md
```

ResumeAgent 执行后返回：
- `{"type": "parse_done", "profile": {...}, "markdown_path": "resumes/张三.md"}`
- `{"type": "brief_done", "candidate_id": "uuid", "brief": "<Markdown>"}`

### InterviewAgent

`generate_suggestion()` 追加本轮任务消息（PromptBuilder 七层之后直接追加，无独立 `_history`）：

```
user: 面试官：{interviewer_text}
      候选人：{candidate_text}

      请结合以上所有对话记录、候选人简历和题目清单，给出一句追问建议或话题切换引导语，直接输出话术，无需解释。
```

LLM 返回**一句中文话术**（非 JSON）。`generate_suggestion()` 使用非流式 `chat()` 调用，一次性 yield 完整文本，由 `_on_trigger_fired` 以 `suggestion_delta` + `suggestion_final` 形式推送至前端。若无面试轮次（面试刚开始）则提示给出开场问题。

### EvalAgent

EvalAgent **不使用** `PromptBuilder.build()`，而是在 `_generate_eval()` 中自行组装 messages。

#### 基础消息结构（两条路径共用）

```
[0] system — EVAL_AGENT_SYSTEM_PROMPT（角色定义 + 评分标准）
[1] system — "## 岗位要求与面试官偏好\n{UserMemoryStore.render()}"（若有内容）
[2] system — "## 候选人信息\n姓名/职位/技能/简历内容(前2000字)"
[3] system — "## 候选人历史面试记录\n..."（若 history_summary 非空）
```

**USER.md** 由 `UserMemoryStore.render()` 在每次 eval 时从内存直接读取，无内容时跳过。

#### 路径一：单次调用（估算 token ≤ 30000）

估算方式：`len(full_text)`（字符数，中文约 1 char/token，更保守）

```
... 基础消息 ...
user: 以下是完整的面试对话记录（共 N 轮）：

      第 1 轮
      面试官: ...
      候选人: ...

      第 2 轮
      ...

      输出 JSON 对象，包含以下字段：
      - dimensions / overall_score / strengths / weaknesses / recommendation / summary
```

#### 路径二：分块 map-reduce（估算 token > 30000）

**Map 阶段**（每 30 轮一块，每块单独调用）：

```
... 基础消息 ...
user: 以下是面试对话的第 {start}–{end} 轮（共 N 轮中的第 k/m 段）：

      第 N 轮
      面试官: ...
      候选人: ...
      ...

      请分析候选人在这部分对话中的表现，输出结构化文字，包含：
      - 每道题候选人的回答质量与深度
      - 体现出的能力亮点（引用候选人原话）
      - 明显的不足或知识盲点
      - 涉及的考察维度判断
```

**Reduce 阶段**（汇总所有局部分析，一次调用）：

```
... 基础消息 ...
user: 以下是对候选人面试各阶段的逐段分析结果（共 N 轮，分 m 段）：

      【第 1–10 轮分析】
      ...

      【第 11–20 轮分析】
      ...

      请综合以上所有分析，生成完整面试评价报告，输出 JSON 对象：
      - dimensions(含 evidence 字段) / overall_score / strengths / weaknesses / recommendation / summary
```

**数据来源**：`session.rounds`（所有原始轮次，仅使用 `interviewer_text` 和 `candidate_text`，不包含 `llm_suggestion`）。

---

## 四、完整 messages 结构示意

以 **InterviewAgent 生成追问建议**为例，完整 messages 列表如下：

```
[0] system  — Layer 1~6 合并（\n\n 拼接）：
              INTERVIEW_AGENT_SYSTEM_PROMPT（直接输出一句中文话术，无 JSON 格式要求）
              （Layer 2 skill index：未配置，跳过）
              （Layer 3 tool guidance：未配置，跳过）
              ＋ "候选人 张三 历史面试记录：..."（Layer 4，若有）
              ＋ "候选人：张三\n当前职位：...\n## 候选人简历\n{resume_content}\n## 面试简报\n{interview_brief}\n## 面试官岗位要求..."（Layer 5）
              ＋ "[以下为早期面试对话的压缩摘要...] ..."（Layer 6，若有）

[1] user    — "面试官：你之前做过哪些高并发项目？\n候选人：我在上家公司负责了一个日活500万的电商系统..."
              （Layer 7 全量历史第 1 轮，面试官+候选人合并；full_history=True 时为全部 rounds）
[2] assistant — "这个系统的峰值 QPS 大概是多少，读写比例怎么分布？"
              （第 1 轮的 llm_suggestion，若有；为一句自然语言话术）

... （全量轮次，非滑动窗口）

[N] user    — "面试官：那你们数据库层怎么做的？\n候选人：我们用了分库分表...\n\n请结合以上所有对话记录、候选人简历和题目清单，给出一句追问建议或话题切换引导语，直接输出话术，无需解释。"
              （本轮触发内容，由 generate_suggestion() 追加）
```

> **注**：
> - Layer 5 固定区包含完整 `resume_content` 和 `interview_brief`，InterviewAgent 无需调用 `file_read` 工具。
> - InterviewAgent 配置 `full_history=True`，Layer 7 使用全量 rounds（非滑动窗口），超出 token 预算时由 `_enforce_token_budget()` 主动截断历史中间段。
> - InterviewAgent 无独立 `_history` 字段，追问上下文完全依赖 ContextManager 的 `session.rounds`。
