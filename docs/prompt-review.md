# 提示词 Review：待确认问题清单

> 来源：对 `docs/arc/prompt-assembly.md` 及相关实现文件的 review。
> 请逐项确认是否修复，确认后在对应条目标记 `[x]`。

---

## 🔴 高优先级

### 1. EvalAgent：全量对话与 Layer 7 滑动窗口重复注入

**位置**：`src/agents/eval_agent.py` `_generate_eval()`，`src/framework/prompt_builder.py` `build()`

**问题**：
`_generate_eval()` 把全部对话轮次拼成 `conversation` 文本放入用户消息；但 `prompt_builder.build()` 同时也通过 Layer 6/7 注入了最近 6 轮的原始对话。最后几轮对话重复出现两次，浪费 token，且可能使 LLM 对"哪份才是权威数据"产生困惑。

**建议修复**：
在 `AgentConfig` 中增加 `skip_context: bool = False` 字段，EvalAgent 的 config 将其设为 `True`，`PromptBuilder.build()` 据此跳过 Layer 6/7 的注入。EvalAgent 的用户消息已包含全量对话，无需滑动窗口。

---

### 2. 子 Agent 无法感知 USER.md 岗位要求

**位置**：`src/framework/prompt_builder.py` `_build_fixed_zone()`，`src/agents/prompts.py`

**问题**：
USER.md（岗位要求、面试风格偏好）仅注入 MainAgent 的系统提示，InterviewAgent 和 EvalAgent 完全无法感知。
- InterviewAgent 追问时不知道岗位方向（如候选人一直聊 Python 但岗位要求 Go）
- EvalAgent 评分时无法以岗位要求为基准校准维度权重

**建议修复**：
将 USER.md 内容（或精简版岗位要求）作为 `InterviewSession` 的字段（如 `session.job_requirements`）传入，并在 `_build_fixed_zone()` 中追加。这样子 Agent 通过 Layer 5 即可感知。

---

### 3. InterviewAgent：双历史结构格式不一致

**位置**：`src/framework/prompt_builder.py` Layer 7，`src/agents/interview_agent.py` `generate_suggestion()`

**问题**：
同一段面试对话以两种格式前后出现在同一个 messages 列表中：

- **Layer 7**（ContextManager）：分三条消息，`role=user`/`user`/`assistant`，前缀 `[面试官]`/`[候选人]`/`[追问建议]`
- **`self._history`**：面试官+候选人合并为一条 `role=user` 消息，内容格式为 `"面试官：...\n候选人最新回答：...\n\n请给出一条追问建议。"`

两套结构描述同一段对话，格式不一致，对 LLM 来说角色语义存在歧义。

**建议修复**：
`self._history` 只保存 AI 的 assistant 建议消息，不重复面试官/候选人的发言（那部分已在 Layer 7 中）。用户消息只传当次触发内容，不再包含冗余的历史对话文本。

---

## 🟡 中优先级

### 4. ResumeAgent 系统提示描述了两步流程，但两步是独立 LLM 调用

**位置**：`src/agents/prompts.py` `RESUME_AGENT_SYSTEM_PROMPT`，`src/agents/resume_agent.py`

**问题**：
`RESUME_AGENT_SYSTEM_PROMPT` 描述"第一步解析简历 → 第二步制定题目清单"，但实际上这是两次独立调用，每次都重新 `build()` 一套新的 messages。`_generate_questions()` 调用时，系统提示仍包含"第一步调用 parse_resume 工具"的指引，与当前执行阶段不符，可能让 LLM 重复调用不必要的工具。

**建议修复**：
拆分为两个独立的系统提示常量：`RESUME_PARSE_PROMPT`（只包含解析指引）和 `QUESTION_GEN_PROMPT`（只包含出题指引）；或在 `AgentConfig` 层面按阶段选择 `system_prompt`。

---

### 5. EvalAgent：JSON 输出 schema 描述混在用户消息的对话数据之中

**位置**：`src/agents/eval_agent.py` `_generate_eval()`，`src/agents/prompts.py` `EVAL_AGENT_SYSTEM_PROMPT`

**问题**：
用户消息格式为：
```
请根据以下完整面试对话记录生成评价报告：

{全量对话文本}

输出 JSON 对象，包含以下字段：
- dimensions: ...
- overall_score: ...
```
schema 说明夹在数据的末尾，且 `EVAL_AGENT_SYSTEM_PROMPT` 已经包含维度体系和评分标准，两处信息分裂、不对齐。

**建议修复**：
把 JSON 字段 schema 移入 `EVAL_AGENT_SYSTEM_PROMPT`（与维度说明合并），用户消息只传对话数据 + 简短触发语（如"请根据以上完整面试对话记录生成评价报告。"）。

---

### 6. MainAgent `_LAYER1_ROLE`：只提到了一个工具，其余工具无使用说明

**位置**：`src/agents/main_agent.py` `_LAYER1_ROLE`

**问题**：
当前只提到 `update_user_memory`，`delegate_to_resume_agent`、`get_session_info`、`get_candidate_info` 三个工具无任何触发说明。特别是 `delegate_to_resume_agent` 是解析简历的关键入口，LLM 不知道何时应主动调用。

**建议修复**：
在 `_LAYER1_ROLE` 的能力描述部分，为每个工具各加一行触发提示，例如：
```
- 当面试官要求解析简历或生成题目时，调用 delegate_to_resume_agent 工具
- 当面试官询问当前面试状态时，调用 get_session_info 工具
- 当面试官询问候选人详情时，调用 get_candidate_info 工具
- 当面试官提供岗位要求或偏好信息时，调用 update_user_memory 工具保存
```

---

## 🟢 低优先级

### 7. InterviewAgent 系统提示对 question_plan 的引用过于隐晦

**位置**：`src/agents/prompts.py` `INTERVIEW_AGENT_SYSTEM_PROMPT`

**问题**：
追问策略第四条写"引导切换到尚未覆盖的维度（参考上下文中的已覆盖维度列表）"，依赖 LLM 自行推断去看 Layer 5 的固定信息区。

**建议修复**：
改为更明确的表述，如"系统提示中的面试题目清单标注了哪些题已覆盖（✓），优先引导候选人回答未覆盖（○）的维度"。

---

### 8. 上下文压缩提示词无格式约束

**位置**：`src/framework/context.py` `_compress_async()`（压缩 LLM 调用的 system 消息）

**问题**：
压缩指令仅为"请将以下面试对话轮次压缩为简洁的结构化摘要，保留候选人的技术亮点、短板和关键回答要点"，没有格式要求，导致摘要结构不可预期，后续 EvalAgent 等 Agent 难以从摘要中稳定提取信息。

**建议修复**：
加上格式约束，如"按考察维度分点总结，每点不超过 2 句话，直接输出纯文本，不加标题"。
