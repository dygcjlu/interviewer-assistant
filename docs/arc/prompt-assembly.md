# 提示词动态组装机制

本文档说明系统中各 Agent 如何在运行时动态组装提示词，包括每层的内容、来源和更新时机。

---

## 总览：两套提示词体系

系统中存在两套并行的提示词组装逻辑，分别服务于不同的 Agent：

| Agent | 提示词体系 | 文件 |
|---|---|---|
| **MainAgent** | 自管理三层系统提示 | `src/agents/main_agent.py` |
| **InterviewAgent / ResumeAgent / EvalAgent** | `PromptBuilder` 七层统一构建 | `src/framework/prompt_builder.py` |

---

## 一、MainAgent 的三层系统提示

MainAgent 是面试官的唯一对话入口，全程常驻，自行管理三个独立的提示词层。每次 `handle_chat()` 调用时，通过 `_build_system_prompt()` 将三层拼接为一条 system message 传给 LLM。

```
_build_system_prompt()
    ├── Layer 1: _LAYER1_ROLE          （固定）
    ├── Layer 2: _layer2_user_memory   （USER.md 内容）
    └── Layer 3: _layer3_candidate     （当前候选人信息）
```

### Layer 1：角色定义（固定）

**来源**：硬编码常量 `_LAYER1_ROLE`

**内容**：
- Agent 角色身份：专业面试助手
- 能力描述：对话理解、简历解析、偏好记忆、状态查询
- 对话风格要求：简洁专业
- 主动行为指引：检测到岗位要求时自动调用 `update_user_memory` 工具

**更新时机**：永不变更

---

### Layer 2：面试官偏好与岗位要求（USER.md）

**来源**：项目根目录的 `USER.md` 文件

**内容**：面试官历次在对话中告知的岗位要求、技术偏好、面试风格等持久化信息

**注入格式**：
```
## 面试官偏好与岗位要求

{USER.md 全文}
```

**更新时机**：
1. 服务启动时，`MainAgent.__init__()` 调用 `_load_user_memory()` 加载一次
2. 面试官在对话中提供新信息 → MainAgent 调用工具 `update_user_memory` 追加写入 `USER.md` → 立即调用 `reload_user_memory()` 刷新本层

---

### Layer 3：当前候选人信息（动态替换）

**来源**：`CandidateProfile` 对象 + 面试题目清单

**内容**（由 `set_candidate_context()` 构建）：
- 候选人姓名、ID
- 当前职位
- 工作年限
- 技能列表（最多 15 项）
- 简历摘要
- 面试题目清单（最多 12 题，含维度标签）

**注入格式**：
```
## 当前候选人信息

当前候选人：{name}（ID: {id}）
职位：{current_position}
工作年限：{years} 年
技能：{skill1}, {skill2}, ...
简历摘要：{summary}
面试题目：
  1. [系统设计] 请设计一个...
  2. [技术深度] 解释一下...
```

**更新时机**：
- 前端选中候选人时，API 层调用 `set_candidate_context(profile, questions)` 替换本层
- 切换候选人时只替换本层，**对话历史不清空**（保持上下文连续）
- 调用 `clear_candidate_context()` 清空本层

---

## 二、子 Agent 的七层统一提示词（PromptBuilder）

`InterviewAgent`、`ResumeAgent`、`EvalAgent` 均通过 `PromptBuilder.build(session, config)` 构建完整的 `messages` 列表。PromptBuilder 是系统唯一对外输出 messages 列表的模块。

```python
messages = self.prompt_builder.build(self._session, self.config)
```

构建结果是一个有序的 `list[Message]`，前七层全部以 `role="system"` 注入，之后由各 Agent 自行追加用户消息。

### 七层结构总览

```
messages = [
    Layer 1: system  — Agent 身份定义
    Layer 2: system  — 可用技巧索引（可选）
    Layer 3: system  — 可用工具说明（可选）
    Layer 4: system  — 候选人历史记忆（可选）
    Layer 5: system  — 面试固定信息区
    Layer 6: system  — 早期对话压缩摘要（可选）
    Layer 7: user/assistant×N — 滑动窗口对话轮次
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
候选人的基本信息已在系统提示中提供。
## 你的核心任务
根据候选人的最新回答，生成一条精准的追问建议...
## 追问策略
- 候选人使用了术语但未展开 → 追问底层原理...
## 输出格式
直接输出追问建议文字，1-2 句话，简洁直接...
```

**更新时机**：固定不变，服务启动时一次性配置

---

### Layer 2：可用技巧索引（Skill Index）

**来源**：`SkillLoader.load_index()` 扫描 `skills/` 目录下的所有 `SKILL.md` frontmatter

**触发条件**：`AgentConfig.skill_names` 非空

**内容**：
```
可用面试技巧：
- star_method: STAR 法则追问技巧 [当候选人描述项目经历时使用]
- deep_dive: 技术深挖策略 [当候选人提到技术名词时使用]
```

**设计说明**：此层只注入技巧的"索引"（名称+简介+触发提示），不注入全文。Agent 需要详情时通过 `skill_view` 工具按需加载，避免 prompt 过大。

**哪些 Agent 使用**：ResumeAgent（`skill_view` 工具）、EvalAgent（`skill_view` 工具）

---

### Layer 3：可用工具说明（Tool Guidance）

**来源**：`ToolRegistry.get_tool(name)` 按名称查询工具描述

**触发条件**：`AgentConfig.tool_names` 非空

**内容**：
```
可用工具：
- parse_resume: 读取 PDF 简历并提取文本
- read_resume_markdown: 读取候选人简历 Markdown 完整内容
- skill_view: 查看指定面试技巧的完整内容
```

**设计说明**：此层是对 LLM tool call 机制的文字补充说明，帮助模型更好地选择工具调用时机。实际工具 schema 通过 `tool_registry.get_schemas()` 以标准 OpenAI tools 格式单独传递。

---

### Layer 4：候选人历史记忆（Candidate Long-term Memory）

**来源**：`session.candidate.history_summary`（由 `MemoryModule.get_candidate_history()` 加载后注入）

**触发条件**：`history_summary` 非空（候选人有历史面试记录）

**内容**（`_format_history_summary()` 格式化）：
```
候选人 张三 历史面试记录：

1. 2026-01-15 10:00 — 综合评分 7.5/10，推荐 hire
   关键发现: 系统设计能力强，算法基础偏弱，Redis 使用经验丰富...

2. 2025-08-20 14:30 — 综合评分 6.0/10，推荐 weak_hire
   关键发现: ...
```

**更新时机**：每次创建新面试会话时，由 API 层从 DB 加载后注入到 `session.candidate.history_summary`

---

### Layer 5：面试固定信息区（Interview Fixed Zone）

**来源**：`_build_fixed_zone(session)` 从 `InterviewSession` 中提取

**内容**（始终存在）：
```
候选人：张三
当前职位：高级后端工程师
工作年限：5 年
年龄：28
教育背景：北京大学 本科 计算机科学
技能：Python, Go, Redis, Kubernetes, MySQL
简历文件路径：resumes/xxx.md（可调用 read_resume_markdown 工具查看完整内容）

简历摘要：
5 年后端开发经验，专注于高并发系统设计...

面试题目清单：
○ [技术深度] 请解释 Redis 的持久化机制及适用场景
✓ [系统设计] 设计一个支持百万 QPS 的短链系统
○ [项目经验] 在你负责的最复杂项目中，遇到的最大挑战是什么

已覆盖维度：系统设计
```

**字段说明**：
- `○`/`✓` 标记各题目是否已被覆盖（`InterviewQuestion.is_covered`）
- `已覆盖维度` 来自 `session.covered_dimensions`，帮助 LLM 引导话题切换

**更新时机**：每次 `build()` 调用时实时读取 `session` 对象，反映最新题目覆盖状态

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

**默认窗口大小**：6 轮（`ContextConfig.window_size = 6`）

**内容**（每轮展开为 2-3 条消息）：
```
user:       [面试官] 请解释一下 Redis 的 AOF 持久化机制
user:       [候选人] AOF 是 Append Only File，每次写操作都会追加到文件末尾...
assistant:  [追问建议] AOF rewrite 的触发条件是什么，rewrite 期间新的写入如何处理？
```

**设计说明**：
- 面试官和候选人的发言均以 `role="user"` 注入，用 `[面试官]`/`[候选人]` 前缀区分
- `llm_suggestion` 字段（上一轮 AI 追问建议）以 `role="assistant"` 注入，形成完整对话链
- 窗口外的历史已被压缩到 Layer 6，此层只保留最近 N 轮原始内容

---

## 三、各 Agent 追加的用户消息

PromptBuilder 的七层是静态背景，各 Agent 在调用 `build()` 后还会追加本次任务的具体指令：

### ResumeAgent

`_parse_resume()` 追加：
```
user: 请解析候选人简历文件：{pdf_path}
      请调用 parse_resume 工具读取 PDF 内容，
      然后以 JSON 格式输出包含以下字段的候选人结构化信息：...
```

`_generate_questions()` 追加：
```
user: 请根据以下候选人结构化信息生成 8-12 道面试题目清单。
      仅输出 JSON 数组，不要调用任何工具。每道题目必须包含字段：...
      候选人信息：
      {...}
```

### InterviewAgent

`generate_suggestion()` 追加：

```python
# PromptBuilder 七层之后，先拼入本次面试的历史追问轮次
messages.extend(self._history)

# 再追加本轮的最新对话内容
user: 面试官：{interviewer_text}
      候选人最新回答：{candidate_text}

      请给出一条追问建议。
```

注意 `self._history` 是 InterviewAgent 自己维护的追问对话历史（最近 10 轮，20 条消息），独立于 ContextManager 的面试对话轮次。

### EvalAgent

`_generate_eval()` 追加：
```
user: 请根据以下完整面试对话记录生成评价报告：

      第 1 轮
      面试官: ...
      候选人: ...

      第 2 轮
      ...

      输出 JSON 对象，包含以下字段：
      - dimensions: ...
      - overall_score: ...
      - recommendation: ...
```

---

## 四、完整 messages 结构示意

以 **InterviewAgent 生成追问建议**为例，完整 messages 列表如下：

```
[0] system  — INTERVIEW_AGENT_SYSTEM_PROMPT（角色定义）
[1] system  — "可用工具：read_resume_markdown: ..."（工具说明）
[2] system  — "候选人 张三 历史面试记录：..."（历史记忆，若有）
[3] system  — "候选人：张三\n当前职位：...\n面试题目清单：..."（固定信息区）
[4] system  — "[以下为早期面试对话的压缩摘要...] ..."（压缩摘要，若有）
[5] user    — "[面试官] 你之前做过哪些高并发项目？"（窗口轮次第1轮）
[6] user    — "[候选人] 我在上家公司负责了一个日活500万的电商系统..."
[7] assistant — "[追问建议] 这个系统的峰值 QPS 大概是多少？..."
... （最多 6 轮 × 3 条）
[N] user    — "[面试官] 那你们数据库层怎么做的？"（上一轮追问历史）
[N+1] assistant — "你们使用了分库分表方案..."
[N+2] user  — "面试官：请讲一下你们的缓存策略\n候选人最新回答：我们用 Redis 做了..."
              （本轮触发内容）
```
