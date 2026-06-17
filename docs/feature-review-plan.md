# 项目 Review 计划

> **用途**：统一管理两类 review 任务——**功能 review**（F 系列）和**代码质量 review**（C 系列）。  
> **执行方式**：依次处理每个任务单元，完成后在"状态"列更新符号。  
> 本文件是 agent 执行 review 循环的**唯一状态台账**，请勿手动修改状态列，其余列可自由调整。

---

## 一、两类 Review 说明

| 类型 | 系列 | 目标 | Agent 行为 |
|------|------|------|-----------|
| **功能 Review** | F1–F6 | 深入分析功能逻辑、提示词质量、记忆机制、数据流闭环 | 阅读代码 + 文档 → 输出发现报告 → 等待确认后决定是否修复 |
| **代码质量 Review** | C1–C5 | 分析测试覆盖缺口、代码质量、安全与性能问题 | 分析代码 → 展示问题列表 → 批准后执行修复 + 补写测试 |

---

## 二、通用规则

### 状态符号

| 符号 | 含义 |
|------|------|
| ⏳ 待检 | 尚未开始 |
| 🔄 进行中 | 当前正在处理 |
| ✅ 已完成 | review 已输出报告 |
| ⏭️ 跳过 | 本轮人工跳过 |

### 严重程度（功能 Review 专用）

| 级别 | 定义 |
|------|------|
| 🔴 Critical | 功能完全失效或数据丢失风险，需立即修复 |
| 🟠 High | 功能在特定场景下失效，或 LLM 行为严重偏离预期 |
| 🟡 Medium | 体验问题或 LLM 行为质量下降，不影响核心流程 |
| 🟢 Low | 优化建议，非必须 |

---

## 三、功能 Review 维度

每个 F 系列任务单元须从以下维度分析，结论以「严重程度 + 问题描述 + 修复建议」格式输出：

| 维度 | 说明 |
|------|------|
| **功能正确性** | 按当前实现逻辑，该功能是否能正确运行？流程上是否有逻辑缺陷或边界条件遗漏？ |
| **提示词质量** | 涉及 LLM 调用的 system prompt / user message 是否准确、完整、无歧义？是否会产生误导性推理？ |
| **记忆更新机制** | 若功能涉及记忆写入（USER.md / candidates/ 文件系统），触发时机是否合理？提示词是否足够引导 LLM 主动更新？更新后是否正确刷新到 prompt？ |
| **数据增删改查** | 涉及数据的创建、读取、更新、删除路径是否完整闭环？是否存在数据不一致或泄漏风险？ |
| **实现最优性** | 当前实现是否有更简洁、更可靠的替代方案？是否存在不必要的复杂性或性能瓶颈？ |
| **其他潜在问题** | 并发安全、错误处理、平台兼容性、Token 预算等其他风险点。 |

输出格式模板：

```
## [F1-1] dispatch_to_agent 工具调用链与副作用机制

### 功能正确性
🟠 High — [问题描述]

### 提示词质量
🟡 Medium — [问题描述]

...（其他维度，无问题的维度可略过）

### 修复建议
- [建议 1]（建议立即修复）
- [建议 2]（可选优化）
```

---

## 四、功能 Review 台账（F 系列）

### F1：简历上传与解析流程

| # | 任务单元 | 核心文件 | 辅助参考 | 状态 |
|---|----------|----------|----------|------|
| F1-1 | `dispatch_to_agent` 工具调用链与副作用机制 | `src/tools/dispatch_to_agent.py` | `docs/arc/agents.md §ResumeAgent` | ✅ 已完成 |
| F1-2 | ResumeAgent 提示词：解析任务 + 简报生成任务 | `src/agents/prompts.py` (RESUME_AGENT_SYSTEM_PROMPT) | `docs/arc/prompt-assembly.md §ResumeAgent` | ✅ 已完成 |
| F1-3 | MainAgent 引导式对话工作流提示词（阶段一/二） | `src/agents/main_agent.py` (_LAYER1_ROLE) | `docs/arc/agents.md §MainAgent` | ✅ 已完成 |
| F1-4 | `parse_done` / `brief_done` 副作用数据写入路径 | `src/tools/dispatch_to_agent.py`, `src/storage/memory_module.py` | `docs/arc/flows.md §1` | ✅ 已完成 |
| F1-5 | 候选人档案 CRUD 完整性（创建/读取/更新/删除） | `src/storage/memory_module.py`, `src/web/routes.py` | `docs/arc/storage.md` | ✅ 已完成 |

**重点关注**：
- ResumeAgent 输出 JSON 格式的约束力（LLM 是否可能输出自然语言导致副作用失败）
- `brief_done` 后 `memory.start_interview(session)` 写入 `session.json` 的时机（与用户手动点击"开始面试"的两步写入是否职责清晰）
- 去重检查（同名候选人 409）与 `overwrite` 参数的行为是否完整覆盖各场景

---

### F2：面试开始与状态机

| # | 任务单元 | 核心文件 | 辅助参考 | 状态 |
|---|----------|----------|----------|------|
| F2-1 | InterviewController 状态机流转与前置条件检查 | `src/agents/interview_controller.py` | `docs/arc/agents.md §InterviewController` | ✅ 已完成 |
| F2-2 | `start_interview()` 音频启动失败不阻断面试的设计 | `src/agents/interview_controller.py`, `src/audio/manager.py` | `docs/arc/flows.md §2` | ✅ 已完成 |
| F2-3 | `create_session` 候选人数据加载（history_summary / resume_content / interview_brief） | `src/agents/interview_controller.py` | `docs/arc/agents.md §create_session` | ✅ 已完成 |
| F2-4 | InterviewSession 跨生命周期数据一致性 | `src/models/session.py`, `src/agents/interview_controller.py` | `docs/arc/agents.md §InterviewSession` | ✅ 已完成 |

**重点关注**：
- 音频启动失败时，面试是否能在"无音频"状态下继续（是否有降级机制或用户提示）
- `session.stage` 的内存状态与 `session.json` 文件中的持久化状态是否始终同步
- 重复调用 `start_interview()` / `stop_interview()` 的幂等性

---

### F3：实时转写与追问建议

| # | 任务单元 | 核心文件 | 辅助参考 | 状态 |
|---|----------|----------|----------|------|
| F3-1 | `TranscriptionManager` 轮次归档逻辑（finalize_round 触发条件） | `src/audio/transcription.py` | `docs/arc/flows.md §3` | ✅ 已完成 |
| F3-2 | `SuggestionTrigger` 触发参数（静默阈值 / 最小间隔）与防抖机制 | `src/audio/trigger.py` | `docs/arc/agents.md §SuggestionTrigger` | ✅ 已完成 |
| F3-3 | InterviewAgent 提示词质量（INTERVIEW_AGENT_SYSTEM_PROMPT） | `src/agents/prompts.py` | `docs/arc/prompt-assembly.md §InterviewAgent` | ✅ 已完成 |
| F3-4 | `generate_suggestion()` 流式 vs 非流式设计与 Token 预算保护 | `src/agents/interview_agent.py` | `docs/arc/prompt-assembly.md §InterviewAgent追加消息` | ✅ 已完成 |
| F3-5 | `ContextManager` 滑动窗口 + 异步压缩策略 | `src/framework/context.py` | `docs/arc/context-memory.md §一` | ✅ 已完成 |
| F3-6 | PromptBuilder Layer 5（固定区）与 Layer 7（滑动窗口）的协同 | `src/framework/prompt_builder.py` | `docs/arc/prompt-assembly.md §七层结构` | ✅ 已完成 |

**重点关注**：
- `finalize_round` 触发条件：面试官新 segment 到来且候选人已有文字时触发——若面试官先发言，候选人文字为空，是否会归档空轮次？
- InterviewAgent prompt 中"题目清单"的描述与实际系统中只有"面试简报"的不一致
- `_enforce_token_budget` 截断策略（保留 8/6/4/2 轮）是否合理，截断后 full_history 的语义完整性
- ContextManager 压缩时 head/tail 截断（丢弃中间轮次）仅影响 InterviewAgent 的实时追问上下文；EvalAgent 读取完整的 `transcript.md` 文件生成评价，**不受 ContextManager 压缩影响**

---

### F4：面试结束与评价生成

| # | 任务单元 | 核心文件 | 辅助参考 | 状态 |
|---|----------|----------|----------|------|
| F4-1 | `stop_interview()` → `flush_pending_round()` → `close_session()` 顺序与数据完整性 | `src/agents/interview_controller.py`, `src/web/routes.py` | `docs/arc/flows.md §4` | ⏳ 待检 |
| F4-2 | EvalAgent 提示词质量（EVAL_AGENT_SYSTEM_PROMPT）与评分维度设计 | `src/agents/prompts.py` | `docs/arc/prompt-assembly.md §EvalAgent` | ⏳ 待检 |
| F4-3 | EvalAgent 单次 vs map-reduce 路径的 Token 估算准确性（字符数≈token数假设） | `src/agents/eval_agent.py` | `docs/arc/prompt-assembly.md §路径一/二` | ⏳ 待检 |
| F4-4 | `close_session()` 失败时数据持久化降级机制 | `src/web/routes.py` (L443–L458), `src/agents/interview_controller.py` | `docs/arc/flows.md §4` | ⏳ 待检 |
| F4-5 | `finish_interview()` 对两级 index.md 的更新（候选人历史记忆更新链路） | `src/storage/memory_module.py` | `docs/arc/storage.md`, `docs/arc/context-memory.md §三` | ⏳ 待检 |

**重点关注**：
- `GET /api/interview/eval` 中：EvalAgent 失败时 `raise HTTPException` 导致 `close_session()` 永不执行，面试数据丢失（`close_session` 已有 3 次重试，但 EvalAgent 本身失败的场景是否有保护？）
- EvalAgent 不调用 `consolidate_memory`（旧版已移除），候选人长期记忆（history_summary）仅靠 `key_findings` 承载是否足够
- EVAL_AGENT_SYSTEM_PROMPT 中"题目清单"的描述与实际不符

---

### F5：MainAgent 与记忆管理

| # | 任务单元 | 核心文件 | 辅助参考 | 状态 |
|---|----------|----------|----------|------|
| F5-1 | MainAgent 三层 system prompt 的内容完整性与更新时机 | `src/agents/main_agent.py` | `docs/arc/prompt-assembly.md §一` | ⏳ 待检 |
| F5-2 | `manage_user_memory` 工具调用时机：主动触发 vs Memory Nudge | `src/agents/main_agent.py` (_LAYER1_ROLE, _NUDGE_SYSTEM) | `docs/arc/context-memory.md §二` | ⏳ 待检 |
| F5-3 | Memory Nudge 机制设计（触发间隔 10 轮、最近 12 条消息、最多 3 次迭代） | `src/agents/main_agent.py` (_background_memory_review) | `docs/arc/agents.md §Memory Nudge` | ⏳ 待检 |
| F5-4 | `_history` 截断（上限 24 条）对工具调用链完整性的影响 | `src/agents/main_agent.py` (_trim_history) | `docs/arc/context-memory.md §五` | ⏳ 待检 |
| F5-5 | 切换候选人时不清空对话历史的设计合理性 | `src/agents/main_agent.py` (set_candidate_context) | `docs/arc/agents.md §MainAgent对话历史` | ⏳ 待检 |
| F5-6 | `UserMemoryStore` 的条目化存储格式与字符上限（3000）的合理性 | `src/storage/user_memory.py` | `docs/arc/context-memory.md §二` | ⏳ 待检 |

**重点关注**：
- `_LAYER1_ROLE` 对 `manage_user_memory` 的引导是否足够精确（何时应调用，何时不应调用，避免普通聊天触发不必要的记忆写入）
- `_NUDGE_SYSTEM` 的提示词是否足够约束 nudge 行为（防止误将临时信息写入持久记忆）
- `_trim_history` 截断时，若截断点落在工具调用链中间（`assistant[tool_calls]` + `tool` 消息对之间），LLM 是否会收到格式非法的消息序列
- Memory nudge 每 10 轮触发一次后台 LLM 调用的成本（简历准备阶段频繁对话时可能触发较多）

---

### F6：候选人长期记忆体系

| # | 任务单元 | 核心文件 | 辅助参考 | 状态 |
|---|----------|----------|----------|------|
| F6-1 | `history_summary` 加载链路（create_session → get_candidate_history → format） | `src/agents/interview_controller.py`, `src/storage/memory_module.py` | `docs/arc/context-memory.md §三.2` | ⏳ 待检 |
| F6-2 | `key_findings` 自动提取逻辑（从 strengths/weaknesses 各取前 2 条）是否足够 | `src/storage/memory_module.py` (save_eval_report) | `docs/arc/context-memory.md §三.2` | ⏳ 待检 |
| F6-3 | 候选人多次面试后，历史记忆的累积与聚合机制（仅靠 index.md 条目，无跨面试摘要） | `src/storage/memory_module.py` | `docs/arc/context-memory.md §三` | ⏳ 待检 |
| F6-4 | rounds.jsonl WAL 崩溃恢复机制完整性 | `src/storage/memory_module.py`, `src/web/routes.py` | `docs/arc/context-memory.md §四` | ⏳ 待检 |

**重点关注**：
- EvalAgent 取消 `consolidate_memory` 后，候选人跨面试的技术成长追踪完全依赖 `key_findings`（各取前 2 条），信息密度是否够用于 InterviewAgent Layer 4 注入
- 若候选人已有 10 次面试记录，`get_candidate_history(limit=3)` 只取最近 3 次，是否需要更长时间跨度的摘要聚合

---

## 五、代码质量 Review 台账（C 系列）

> 覆盖率数据来源：`pytest tests/unit --cov=src`（2026-06-04）  
> C 系列 review 完成后，agent 需执行：**修复问题 + 补写测试至 80% 覆盖率目标**。

### C1：核心业务逻辑（优先处理）

| 模块 | 路径 | 覆盖率 | 状态 | 上次检查 | 重点检查项 |
|------|------|--------|------|----------|------------|
| main_agent | `src/agents/main_agent.py` | **12%** | ⏳ 待检 | — | 测试覆盖严重不足；工具调用流、状态机逻辑 |
| interview_controller | `src/agents/interview_controller.py` | **61%** | ⏳ 待检 | — | 面试生命周期控制、边界条件处理 |
| interview_agent | `src/agents/interview_agent.py` | 81% | ⏳ 待检 | — | 追问逻辑、错误恢复路径 |
| eval_agent | `src/agents/eval_agent.py` | 75% | ⏳ 待检 | — | 评价生成完整流程测试 |
| resume_agent | `src/agents/resume_agent.py` | 85% | ⏳ 待检 | — | PDF 解析失败路径 |

### C2：存储与框架（数据一致性关键）

| 模块 | 路径 | 覆盖率 | 状态 | 上次检查 | 重点检查项 |
|------|------|--------|------|----------|------------|
| memory_module | `src/storage/memory_module.py` | 86% | ⏳ 待检 | — | 633 行大文件；滑动窗口/压缩逻辑；并发安全 |
| context | `src/framework/context.py` | 86% | ⏳ 待检 | — | ContextManager 压缩触发逻辑；token 计算 |
| prompt_builder | `src/framework/prompt_builder.py` | 73% | ⏳ 待检 | — | 七层提示词组装；测试缺口 |
| user_memory | `src/storage/user_memory.py` | 82% | ⏳ 待检 | — | 面试官记忆持久化；并发写 |
| conversation_logger | `src/storage/conversation_logger.py` | 88% | ⏳ 待检 | — | 文件写入原子性 |

### C3：音频模块（平台依赖，Mock 覆盖）

| 模块 | 路径 | 覆盖率 | 状态 | 上次检查 | 重点检查项 |
|------|------|--------|------|----------|------------|
| audio/manager | `src/audio/manager.py` | **22%** | ⏳ 待检 | — | 双声道采集协调；启停逻辑 |
| audio/recorder | `src/audio/recorder.py` | **31%** | ⏳ 待检 | — | 录音文件管理；错误恢复 |
| audio/trigger | `src/audio/trigger.py` | 91% | ⏳ 待检 | — | 追问触发条件 |
| audio/transcription | `src/audio/transcription.py` | 86% | ⏳ 待检 | — | 转写结果聚合 |
| audio/mock_manager | `src/audio/mock_manager.py` | 48% | ⏳ 待检 | — | Mock 管理器完善 |
| audio/script_player | `src/audio/script_player.py` | **32%** | ⏳ 待检 | — | 脚本播放器测试 |
| baidu_stt | `src/audio/baidu_stt.py` | **0%** | ⏳ 待检 | — | 完全无测试；WebSocket ASR 客户端 |
| wasapi | `src/audio/wasapi.py` | **0%** | ⏳ 待检 | — | Windows 平台专属；集成测试 + Mock |
| xunfei_stt | `src/audio/xunfei_stt.py` | **0%** | ⏳ 待检 | — | 完全无测试；讯飞 ASR 客户端 |

### C4：Web 层（端到端测试为主）

| 模块 | 路径 | 覆盖率 | 状态 | 上次检查 | 重点检查项 |
|------|------|--------|------|----------|------------|
| web/routes | `src/web/routes.py` | **0%** | ⏳ 待检 | — | 所有 REST 接口；输入验证 |
| web/websocket | `src/web/websocket.py` | **0%** | ⏳ 待检 | — | WebSocket 消息处理；断连恢复 |
| web/ui | `src/web/ui.py` | **0%** | ⏳ 待检 | — | NiceGUI 组件；E2E 测试 |
| web/middleware | `src/web/middleware.py` | **0%** | ⏳ 待检 | — | 中间件；请求日志；错误处理 |
| web/app | `src/web/app.py` | **0%** | ⏳ 待检 | — | 应用初始化 |

### C5：工具与其他（按需处理）

| 模块 | 路径 | 覆盖率 | 状态 | 上次检查 | 重点检查项 |
|------|------|--------|------|----------|------------|
| llm/client | `src/llm/client.py` | 90% | ⏳ 待检 | — | 流式响应；重试逻辑；token 统计 |
| tools/dispatch | `src/tools/dispatch_to_agent.py` | 86% | ⏳ 待检 | — | 工具分发；错误传播 |
| tools/_loader | `src/tools/_loader.py` | **50%** | ⏳ 待检 | — | 工具加载器覆盖不足 |
| pdf_parsers/mineru | `src/tools/pdf_parsers/mineru_parser.py` | **62%** | ⏳ 待检 | — | MinerU 解析器 |
| pdf_parsers/qwen_vl | `src/tools/pdf_parsers/qwen_vl_parser.py` | 73% | ⏳ 待检 | — | Qwen VL 解析器 |
| utils/atomic_io | `src/utils/atomic_io.py` | 68% | ⏳ 待检 | — | 原子写入边界情况 |
| logging/context | `src/logging/context.py` | 82% | ⏳ 待检 | — | 日志上下文 |
| main | `src/main.py` | **0%** | ⏳ 待检 | — | 启动流程；lifespan 测试 |

---

## 六、端到端功能测试清单

> 独立于单元测试，覆盖完整用户流程。每项对应 `tests/e2e/` 中的测试用例。

| 功能流程 | 测试文件 | 状态 | 备注 |
|----------|----------|------|------|
| 简历上传 → 解析 → 存档 | `test_full_flow.py` | ⏳ 待检 | 需浏览器自动化 |
| 面试准备（生成简报） | `test_full_flow.py` | ⏳ 待检 | |
| 面试开始 → 转写 → 追问建议 | `test_full_flow.py` | ⏳ 待检 | Mock 音频 |
| 面试结束 → 评价报告生成 | `test_full_flow.py` | ⏳ 待检 | |
| 多候选人管理 | — | ⏳ 待检 | 需补充测试 |
| WebSocket 断连重连 | — | ⏳ 待检 | 需补充测试 |

---

## 七、整体指标快照

> 基线数据采集时间：2026-06-04

| 指标 | 当前值 | 目标 |
|------|--------|------|
| 单元测试总数 | 357 | 500+ |
| 单元测试通过率 | 100% | 100% |
| 代码覆盖率（单元） | **50%** | **80%+** |
| 集成测试数 | ~40 | 60+ |
| E2E 测试数 | ~1 | 10+ |

---

## 八、执行顺序建议

### 功能 Review（F 系列）推荐顺序

```
F1（简历解析）→ F2（面试开始）→ F3（实时追问）→ F4（评价生成）→ F5（记忆管理）→ F6（长期记忆）
```

每次处理一个任务单元（如 F1-1），完成后更新状态列，并将结论汇总到进度记录表。

### 代码质量 Review（C 系列）推荐顺序

```
C1（核心业务）→ C2（存储框架）→ C3（音频模块）→ C4（Web 层）→ C5（工具）
```

每个模块：分析代码 → 展示问题列表 → 用户确认后修复 + 补写测试 → 重跑覆盖率验证 → 更新状态。

---

## 九、进度记录

| 日期 | 操作 | 任务单元 | 结果摘要 |
|------|------|----------|----------|
| 2026-06-04 | 初始化覆盖率台账 | — | 覆盖率基线 50% |
| 2026-06-08 | 合并功能 review 计划 | — | F1–F6 / C1–C5 统一台账 |
| 2026-06-08 | 功能 review | F1-1~F1-5 | 简历解析流程 review 完成，输出 12 项发现（3 High / 5 Medium / 4 Low），详见 review-findings.md |
| 2026-06-08 | 功能 review | F2-1~F2-4 | 面试开始与状态机 review 完成，输出 11 项发现（2 High / 4 Medium / 5 Low），详见 review-findings.md |
| 2026-06-08 | 功能 review | F3-1~F3-6 | 实时转写与追问 review 完成，输出 15 项发现（0 High / 5 Medium / 10 Low），含 2 处设计正确性确认；详见 review-findings.md |
|| 2026-06-08 | 功能 review | F4-1~F4-5 | 面试结束与评价生成 review 完成，输出 11 项发现（1 Critical / 1 High / 2 Medium / 7 Low）；发现关键 Bug F4-5（save_eval_report 先于 finish_interview 执行导致 key_findings 永远为空）；详见 review-findings-f4-f6.md |
|| 2026-06-08 | 功能 review | F5-1~F5-6 | MainAgent 与记忆管理 review 完成，输出 12 项发现（0 Critical / 0 High / 6 Medium / 6 Low）；关键问题：_trim_history 截断可能切断 tool call pair；详见 review-findings-f4-f6.md |
|| 2026-06-08 | 功能 review | F6-1~F6-4 | 候选人长期记忆体系 review 完成，输出 8 项发现（1 Critical 依赖 F4-5 / 2 Medium / 5 Low）；F4-5 修复后 F6-1 自动修复；详见 review-findings-f4-f6.md |
