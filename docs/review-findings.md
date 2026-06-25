# 功能 Review 发现记录（F 系列）

> 本文件记录功能 Review（F1–F6）输出的详细发现。状态台账见 `docs/feature-review-plan.md`。
> Review 阶段仅记录问题，不修复。严重程度定义见计划文档第二节。

---

## F1：简历上传与解析流程

> Review 日期：2026-06-08 ｜ 共 12 项发现：3 High / 5 Medium / 4 Low

### [F1-1] dispatch_to_agent 工具调用链与副作用机制

**功能正确性 — 🟠 High：副作用静默失败仍返回成功。**
`_apply_side_effects` 整体包在 `try/except`（`dispatch_to_agent.py:57-60`），`save_candidate` 内部还有一层 `try/except`（L131-132）。若 `save_candidate` 抛异常，仅写日志，返回给 LLM 的仍是 `{"type":"parse_done"}` 成功结果。结果：LLM 告知"解析完成"，但 `profile.md` 实际未落盘，候选人档案丢失且无用户可见提示。

**数据增删改查 — 🟡 Medium：空简历 warning 不保证透传。**
空简历保护（L120-125）只在 `result` 塞入 `warning` 字段，但 MainAgent 不强制透传 `warning`（不像 `user_facing` 错误有专门短路逻辑），LLM 可能忽略。

**实现最优性 — 🟢 Low：** `enum: ["resume"]` 写死单一 agent，两类任务（解析/简报）靠自然语言关键字区分，无显式 `task_type`，增加 LLM 误判风险。

**修复建议：**
- 区分可恢复/致命副作用：`save_candidate` 失败应升级为 `user_facing` 或在结果标记 `persisted: false`（建议立即修复）
- 空简历 `warning` 纳入 `user_facing` 提示通道（可选优化）

### [F1-2] ResumeAgent 提示词（解析 + 简报生成）

**提示词质量 — 🟡 Medium：** `profile` 要求必含 `age`（`prompts.py:32`），简历少有年龄信息，强制要求诱导 LLM 编造。建议改为"无则省略"。

**功能正确性 — 🟡 Medium：简报任务无 fallback 兜底。**
`_fallback_from_messages`（`resume_agent.py:118-160`）仅能从 `file_write(.md)` 重建 `parse_done`；简报任务无 `file_write` 副作用，若 LLM 最终输出非 JSON，直接 `error`，整份简报丢失。解析任务有兜底、简报任务没有，保护不对称。

**实现最优性 — 🟢 Low：** JSON 提取链容错完善，无明显问题。

**修复建议：**
- `age` 改可选字段，提示"无则省略，勿编造"（建议立即修复，影响数据质量）
- 简报任务也先 `file_write` 临时文件再返回，使其可被 fallback 兜底（可选优化）

### [F1-3] MainAgent 引导式对话工作流提示词（阶段一/二）

**功能正确性 / 记忆更新机制 — 🟠 High：parse_done 后未刷新 Layer 3。**
`_apply_side_effects` 的 `parse_done` 分支只更新 `session.candidate` 和 `resume_content`，**未调用** `main_agent.set_candidate_context()`（对比 `brief_done` 分支 L154 有调用）。结果：进入"阶段一：候选人分析呈现"时，MainAgent 系统提示 Layer 3 仍是旧/空内容，LLM 手里没有完整简历正文（`resume_content` 全文不在工具返回 JSON 中），显著降低阶段一风险信号识别与分析质量。

**提示词质量 — 🟡 Medium：** 阶段二 `task="为候选人[ID]生成面试简报..."` 的 ID 依赖 Layer 3 的 `ID: {id}`，而 parse 后 Layer 3 未刷新，存在 ID 取不到的链式风险。

**修复建议：**
- `parse_done` 副作用补调 `main_agent.set_candidate_context(session.candidate, ...)`，使阶段一拿到完整 `resume_content`（建议立即修复，阶段一质量根因）

### [F1-4] parse_done / brief_done 副作用数据写入路径

**功能正确性 — 🟠 High：brief_done 提前写 stage=interviewing。**
`dispatch_to_agent.py:146-151`：只要 `profile.md` 存在，简报生成后立即 `start_interview(session)`，落盘 `session.json(stage="interviewing")`，但此刻面试未真正开始。若简报后未点击"开始面试"或中途切换候选人（`create_session` 换新 `session.id`），残留 `stage=interviewing` 且无 rounds 的孤儿面试目录，`rebuild_index` 会计入历史。

**数据增删改查 — 🟡 Medium：文档与代码不一致。**
`docs/arc/flows.md §2`（L117）写"`InterviewController.start_interview()` … 不再重复调用 `memory.start_interview()`"，但 `interview_controller.py:295-298` 实际仍调用。`session.json` 被写两次（brief 时、真正开始时各一次），幂等无害但文档失真，且印证"提前写入"问题。

**修复建议：**
- 将 `brief_done` 的 `start_interview()` 改为只写轻量"简报已生成"标记或移除，把 `session.json(stage=interviewing)` 唯一交给 `InterviewController.start_interview()`（建议立即修复）
- 同步修正 `docs/arc/flows.md §2` 描述（建议立即修复）

### [F1-5] 候选人档案 CRUD 完整性

**数据增删改查 — 🟡 Medium：去重依赖文件名而非真实姓名。**
上传时 `get_candidate_by_name(safe_stem)`（`routes.py:175`）用 PDF 文件名 stem 比对已存档候选人的 `name`（解析出的真实姓名）。仅当历史候选人姓名恰等于本次文件名时命中；文件名为 `resume_v2.pdf` 时去重完全失效，静默产生重复候选人。

**数据增删改查 — 🟡 Medium：`/candidates` 的 total 不准确。**
`routes.py:478` 调 `search_candidates(keyword, limit=limit+offset)`，再 `return {"total": len(candidates)}`。`total` 被 `limit+offset` 截断，非真实总数，前端分页失真。

**数据增删改查 — 🟢 Low：删除候选人不清理录音。**
`delete_candidate` 只 `rmtree(candidates/{id}/)`，录音存于 `recordings/{session_id}/`（独立目录），删除后录音成孤儿。

**实现最优性 — 🟢 Low：** 无独立候选人资料编辑接口（无 PUT/PATCH），更新档案只能"重新上传 + overwrite 重解析"。单用户工具可接受的 YAGNI 取舍。

**其他潜在问题 — 🟢 Low：** `index.md` 读-改-写（`save_candidate`/`delete_candidate`）无文件锁，并发写竞态。单用户风险低。

**修复建议：**
- 去重改为解析得真实姓名后再判同名，或 UI 明确提示"按文件名去重"（建议修复）
- `/candidates` 返回真实总数（建议修复）
- `delete_candidate` 关联删除该候选人各 session 的 `recordings/` 目录（可选优化）

---

## F2：面试开始与状态机

> Review 日期：2026-06-08 ｜ 共 11 项发现：2 High / 4 Medium / 5 Low

### [F2-1] InterviewController 状态机流转与前置条件检查

**功能正确性 — 🟠 High：start_interview 只防 INTERVIEWING，不防 EVALUATING/COMPLETED。**
`_start_interview_impl`（`interview_controller.py:202-209`）仅在 `stage == INTERVIEWING` 时 raise。但 `stop_interview` 后 stage=EVALUATING 且 session 仍存活（`close_session` 未调用）。此时再次 start 会通过校验，重新 `on_activate` + 启动音频，把评价中的会话拉回面试，破坏状态机。应要求 `stage == IDLE`。

**功能正确性 — 🟡 Medium：stop_interview 无 stage 前置校验。**
对从未 start 的 IDLE 会话调用 stop（路由仅校验 `session is not None`，`routes.py:364-366`），仍执行 flush/deactivate/audio.stop，并按 rounds 数置 COMPLETED——但 `session.json` 从未写过（start 未跑），后续 `close_session→finish_interview` 凭空写出 0 轮 transcript。

**实现最优性 — 🟢 Low：** `InterviewStage.RESUME_ANALYSIS`（`session.py:13`）定义后全代码无使用，死枚举值。

**修复建议：**
- start_interview 前置条件改为 `stage == IDLE`（建议立即修复）
- stop_interview 增加 stage 校验，仅 INTERVIEWING 可停（建议修复）
- 移除 RESUME_ANALYSIS 死枚举（可选优化）

### [F2-2] start_interview() 音频启动失败不阻断面试的设计

**功能正确性 — 🟠 High：AudioManager.start() 无部分失败回滚，资源泄漏。**
`manager.py:43-106` 顺序执行 7 步：连接两个 STT（步骤4）、创建两个 receive-loop task（步骤5）、启动 capturer（步骤6）。若步骤6/7 抛错，异常传到 controller 的 except（`interview_controller.py:271`），但已连接的 STT WebSocket 和已创建的 task 不会清理。每次失败启动都泄漏 STT 连接与孤儿 task。

**功能正确性 — 🟡 Medium：失败后残留半初始化 transcription_manager。**
`manager.start()` 步骤1先创建 `_transcription_manager`，后续步骤失败时它仍非 None，controller 在 except 分支不重置它。`controller.transcription_manager` 返回 bridge/STT 已死的 TM；stop 仍会对它 `flush_pending_round()`。

**功能正确性 — 🟢 Low：suggestion_trigger is None 时音频整段被跳过且无降级提示。**
整段音频启动包在 `if trigger is not None`（`interview_controller.py:229`）内；trigger 为 None 时音频不启动也不广播 `audio_status`，UI 无从得知。

**其他 — 🟢 Low：** 降级设计本身（捕获异常、广播 `audio_status ok:false`、继续 interviewing）合理；问题在失败后清理与状态一致性。

**修复建议：**
- `AudioManager.start()` try/except 包裹，失败回滚已连 STT、取消已建 task、置空 `_transcription_manager`（建议立即修复）
- controller 捕获音频异常后显式将 TM 视为不可用（建议修复）

### [F2-3] create_session 候选人数据加载

**功能正确性 — 🟡 Medium：未知 candidate_id 静默生成空档案。**
`_create_session_impl`（`interview_controller.py:97-107`）：传入 candidate_id 但 `get_candidate` 返回 None 时，直接 `CandidateProfile(id=candidate_id, name="")` 不报错。拼写/已删 id 得到空候选人会话，问题被掩盖到面试中途。

**数据增删改查 — 🟢 Low：** 数据加载链完整（candidate + history_summary + resume_content + interview_brief 均加载），无遗漏。

**修复建议：**
- 已知 candidate_id 但档案不存在时明确报错（路由 404），而非静默空档案（建议修复）

### [F2-4] InterviewSession 跨生命周期数据一致性

**数据增删改查 — 🟡 Medium：内存 stage 与 session.json 在 EVALUATING 阶段不同步。**
`stop_interview` 把内存 stage 置 EVALUATING/COMPLETED，但不写 session.json（仅 `close_session→finish_interview` 写 `stage=completed`）。评价中崩溃则 session.json 仍 `interviewing`——被 `scan_orphan_wal` WAL 恢复兜住，但窗口期两处 stage 语义不一致。

**实现最优性 — 🟢 Low：多个 metadata 死字段。**
`total_rounds`、`total_prompt_tokens`、`total_completion_tokens`（`session.py:36-38`）全代码无写入；API 的 total_rounds 实际用 `len(session.rounds)`（`routes.py:372`）。`ConversationRound.interviewer_audio_path/candidate_audio_path`（`session.py:25-26`）从未填充。

**实现最优性 — 🟢 Low：** `metadata.start_time` 语义是"会话创建时刻"非"面试开始时刻"，在 `create_session` 即固定（常在简历上传时），session.json 的 start_time 可能远早于实际开始。

**修复建议：**
- 移除或真正维护 `total_*` 与 per-round audio 死字段（可选优化）
- stop_interview 时把 `stage=evaluating` 也持久化（可选优化）
- 如需精确时长，真正 start 时另记 `interview_start_time`（可选优化）

---

## F3：实时转写与追问建议

> Review 日期：2026-06-08 ｜ 共 15 项发现：0 High / 5 Medium / 10 Low（含 2 处设计正确性确认 + 1 处可更新计划的澄清）

### [F3-1] TranscriptionManager 轮次归档逻辑

**功能正确性 — ✅ 空轮次防护正确（验证计划重点关注项）。**
`on_segment` interviewer 分支仅在 `candidate_text` 非空时才 `finalize_round()`（`transcription.py:70-72`）。面试官先发言、候选人文字为空时不会归档空轮次。`_silence_timeout` 同样只在 `candidate_text` 非空时 finalize。设计正确。

**其他 — 🟢 Low：两个并发 STT 接收循环无显式同步。**
`AudioManager` 启两个 task（`manager.py:93-98`）并发调 `on_segment`，共享改写文本并触发 finalize，无锁，靠 await 位置保证正确性，重构易引入竞态。

**其他 — 🟢 Low：** 末轮未答问题（仅 interviewer_text）不被 60s 静默计时器强制归档，依赖 stop 的 flush 兜底。

**修复建议：** 为轮次状态加 Lock 或注明并发约束（可选优化）。

### [F3-2] SuggestionTrigger 触发参数与防抖机制

**功能正确性 — 🟡 Medium：min_interval 命中冷却时直接丢弃不重排。**
`_silence_timer`（`trigger.py:95-100`）：2s 静默后若距上次触发 <5s 仅 `return` 不重排。若该静默落在冷却窗内且此后无新 final segment，则该次建议被永久丢弃。

**实现最优性 — 🟢 Low：** auto 用 `trigger._request_id`、manual 用 `agent._request_counter`，两套 id 命名空间，前端关联可能混乱。

**修复建议：** 冷却命中改为延迟到冷却结束再触发（建议修复）；统一 request_id（可选）。

### [F3-3] InterviewAgent 提示词质量

**提示词质量 — 🟡 Medium："题目清单"术语与实际产物不符（验证计划重点关注项）。**
`generate_suggestion` user 消息三处写"题目清单"（`interview_agent.py:240,247,250`），实际产物是"面试简报"（固定区标签 `## 面试简报`）。系统提示用词正确，但 user 消息让 LLM 参考不存在的"题目清单"。

✅ 系统提示其余质量高（两原则、STAR、50 字限制、强制中文）。

**修复建议：** user 消息"题目清单"统一改为"面试简报"（建议立即修复）。

### [F3-4] generate_suggestion 流式 vs 非流式与 Token 预算保护

**功能正确性 — 🟡 Medium：实际为非流式。**
`generate_suggestion` 调 `llm_client.chat()`（非 chat_stream，`interview_agent.py:267`），一次性 yield 完整文本（L289）。`suggestion_delta` 只发一个含整句的 delta，无逐 token 流式，实时体验欠佳。

**功能正确性 — 🟡 Medium：cancel-previous 逻辑在触发路径下失效（已运行时验证）。**
`_on_trigger_fired` 在 `generate_suggestion` 执行前已把 `_current_stream_task` 指向新 `_runner`（L411）。`generate_suggestion` 的取消块（L214-222）取消的是自己：await 自身 task 抛 CancelledError 被吞、取消被消费 → 任务继续（建议不会丢失）。但真正的上一个 runner 从未被取消 → LLM 慢于 min_interval 时两建议并发。该取消等于空操作。

**实现最优性 — 🟢 Low：** `_enforce_token_budget` 访问 `context_manager._config` 私有属性；但硬预算保护本身扎实（精确 count_tokens，8/6/4/2 递减截断）。

**修复建议：** 改用 chat_stream 真流式（建议修复）；取消职责收归 `_on_trigger_fired`，移除 generate_suggestion 内自引用取消（建议修复）。

### [F3-5] ContextManager 滑动窗口 + 异步压缩策略

**功能正确性 — 🟡 Medium：Phase-2 head/tail 截断永久丢弃中间轮次（计划重点关注项，影响被高估）。**
`_compress_async`（`context.py:217-218`）压缩前 `pruned = pruned[:2] + pruned[-TAIL:]`，中间轮次在送 LLM 摘要前丢弃，既不进摘要也不保留。**但仅影响实时 InterviewAgent 上下文**：EvalAgent 用 `session.rounds`（完整 transcript，`eval_agent.py:104`）评价，不经 ContextManager。计划担心的"影响评价 evidence"实际被规避。

**实现最优性 — 🟢 Low：** token 估算 `len//3`（`context.py:171-177`）低估中文 token，压缩偏晚；被 `_enforce_token_budget` 硬上限兜住。

**实现最优性 — 🟢 Low：** 连续 2 次无效压缩后永久跳过压缩（`context.py:92-93`），`_all_rounds` 内存无界增长，靠每次调用截断。

**实现最优性 — 🟢 Low：** 极端兜底 `pruned[:1]`（`context.py:231`）仅摘要 1 轮，丢失量大但罕见。

**修复建议：** 将计划 F3-5 的"影响评价 evidence"修正为"仅影响实时建议上下文"（建议更新文档）；token 估算改 count_tokens 或中文用 ~1:1 系数（可选）。

### [F3-6] PromptBuilder Layer 5（固定区）与 Layer 7（滑动窗口）协同

**功能正确性 — ✅ 协同设计正确。**
InterviewAgent 配 `full_history=True` + `include_suggestions=False`（`main.py:117-118`）。Layer 7 取 `all_rounds`（压缩后=保留近期），Layer 6 摘要覆盖被压缩的早期，并集=完整覆盖无盲区，且不回灌自身历史建议。

**实现最优性 — 🟢 Low：** `prompt_builder.py:79` 注释称固定区含"题目清单"，实际构建"## 面试简报"（同源术语漂移）。

**实现最优性 — 🟢 Low：** `covered_dimensions` 全链路打通但从未被调用/消费，死特性。

**实现最优性 — 🟢 Low：** `eval_config` 未设 `full_history`/`include_suggestions`，但 EvalAgent 不用 `prompt_builder.build` 拼轮次（直接用 `session.rounds`），两配置对其形同虚设、易误导。

**修复建议：** 修正"题目清单"注释；移除或接入死特性 covered_dimensions（可选优化）。
