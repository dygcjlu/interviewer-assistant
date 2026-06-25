# 功能 Review 发现记录（F4–F6）

> 本文件记录功能 Review（F4–F6）输出的详细发现。状态台账见 `docs/feature-review-plan.md`。
> Review 阶段仅记录问题，不修复。严重程度定义见计划文档第二节。

---

## F4：面试结束与评价生成

> Review 日期：2026-06-08 ｜ 共 11 项发现：1 Critical / 1 High / 2 Medium / 7 Low

### [F4-1] stop_interview() → flush_pending_round() → close_session() 顺序与数据完整性

**功能正确性 — ✅ flush 顺序正确。**
`_stop_interview_impl` 先从 `self._audio.transcription_manager` 取引用，调用 `flush_pending_round()`，再调 `audio.stop()`，顺序无误。`TranscriptionManager.finalize_round()` 直接向 `session.rounds` append（`transcription.py:98`），WAL 由 `_on_round_finalized` 回调写入，两条路径各司其职。

**功能正确性 — 🟠 High：EvalAgent 失败时 close_session() 永不执行，session 僵死。**
`GET /interview/eval` 流程（`routes.py:432-436`）：若 `eval_agent.handle_request()` 返回 `success=False`，直接 `raise HTTPException(500)`，后续 `close_session()` 重试块（L443-459）永远不执行。后果：
- `finish_interview()` 未调用 → `transcript.md` 不写入、`interviews/index.md` 不更新
- `session.json` 仍显示 `stage: interviewing`
- 内存中 session 永远停在 `EVALUATING` 状态直到进程重启
- 恢复路径：WAL（`rounds.jsonl`）已实时 append，可通过 `scan_orphan_wal` + `recover_interview_from_wal` 手动恢复

**其他 — 🟢 Low：lifespan 关闭 EVALUATING session 处理正确。**
`_close_session_impl` 的 `if stage == INTERVIEWING` 判断确保不重复 stop audio；`finish_interview()` 在任何 stage 下都会调用，lifespan 路径不受此问题影响。

**修复建议：**
- 在 `GET /interview/eval` 中，EvalAgent 失败时先执行 `close_session()`（写 transcript），再 raise 错误（建议立即修复）
- 或在 `stop_interview()` 中即触发 `finish_interview()`（不含 eval 数据），解耦 eval 失败与 transcript 写入（可选方案）

---

### [F4-2] EvalAgent 提示词质量（EVAL_AGENT_SYSTEM_PROMPT）与评分维度设计

**提示词质量 — 🟡 Medium："题目清单"术语与实际产物不符。**
`EVAL_AGENT_SYSTEM_PROMPT`（`prompts.py:151,162`）两处引用"题目清单"：
- `"若题目清单中某维度未被实际问到，在 dimension 评分中注明'未考察'，score 填 0"`
- `"如果题目清单中涉及以上维度之外的考察点，可以额外增加维度进行评价"`

但 EvalAgent 实际收到的上下文（`_build_candidate_context()` → `prompts.py:374`）是 `## 面试简报`，而非"题目清单"。LLM 被要求参考一个不存在的文档，导致：
1. 维度"未考察"（score=0）的触发逻辑不可靠
2. 额外维度的扩展逻辑不可靠

✅ 其余质量良好：ASR 噪声处理说明（Lines 136-139）体贴周到；evidence 不得为空约束明确；summary 200 字最低要求合理；recommendation 四档选项清晰。

**修复建议：**
- 将"题目清单"统一改为"面试简报"（建议立即修复，与 F3-3 同源问题）

---

### [F4-3] EvalAgent Token 估算准确性（字符数≈token数假设）

**实现最优性 — 🟢 Low：对中文精确，对英文过保守。**
估算逻辑（`eval_agent.py:111-112`）：
```python
estimated_tokens = len(full_text) + system_text_len
```
注释说明"中文每字约 1 token"，对纯中文基本成立（1 汉字 ≈ 1 token）。但技术面试转写常含大量英文词汇/代码，英文中 `len()` 约为实际 token 数的 2 倍（"distributed system" = 16 chars 但约 3 tokens）。

实际影响：英文密集的转写会比实际早触发 map-reduce 路径（30000 字符阈值下，英文密集内容约 15000 真实 token 时就切换），增加不必要的 API 调用延迟。不影响正确性。

**实现最优性 — 🟢 Low：map-reduce reduce 阶段无 token 预算保护。**
`_eval_chunked` reduce 阶段将所有 `partial_analyses` 拼接后一次性发出（`eval_agent.py:334-346`），如 chunks 很多且每段分析较长，reduce 请求本身也可能超窗口。极端情况（100+ 轮面试）有风险。

**修复建议：**
- 可选：将估算改为中文 1:1、英文字符数除以 4（可选优化）
- 可选：reduce 阶段也做 token 检查，超限时分批 reduce（可选优化）

---

### [F4-4] close_session() 失败时数据持久化降级机制

**功能正确性 — 🟢 Low：3 次重试基本是无效保护。**
`routes.py:443-459` 的 3 次重试设计良好，但 `_close_session_impl` 内部已对所有子步骤（audio.stop、on_deactivate、finish_interview）单独 try/except，且 `finally` 块无条件将 `_session = None`。因此 `close_session()` 在大多数情况下不会 raise，3 次重试实际上基本不触发。

✅ 降级路径正确：
- `save_eval_report()` 先于 `close_session()` 执行（eval_agent.py:189），eval 报告落盘后才关闭会话，所以 close_session 失败不会丢失评价数据
- `finish_interview()` 失败时，WAL（rounds.jsonl）仍完整，可由 `scan_orphan_wal` 恢复

---

### [F4-5] finish_interview() 对两级 index.md 的更新（候选人历史记忆更新链路）

**数据增删改查 — 🔴 Critical：save_eval_report 在 finish_interview 之前执行，interviews/index.md 中 eval 数据永远为 NULL。**

实际执行顺序（`GET /interview/eval` → `routes.py`）：
1. `eval_agent.handle_request()` → `_generate_eval()` → **`save_eval_report()`**
2. `close_session()` → `finish_interview()` → 写 `interviews/index.md` 新条目

`save_eval_report()` 逻辑（`memory_module.py:877-892`）：
```python
interviews = self._read_interviews_index(candidate_id)
for iv in interviews:   # 此时 finish_interview 尚未运行，本次面试条目不存在！
    if iv.get("interview_id") == report.interview_id:
        iv["overall_score"] = ...
        break
self._write_interviews_index(...)  # 写回时无本次面试条目，eval 数据被丢弃
```

`finish_interview()` 随后才创建 `interviews/index.md` 条目，初始化时 `overall_score=None`、`recommendation=None`、`key_findings=""`。虽然 `finish_interview` 会保留 existing entry 的 eval 字段（`L728-731`），但此时 entry 根本不存在，只会走 `else: interviews.insert(0, iv_entry)` 分支，eval 数据永久丢失在 index 中。

**实际影响链**：
- `get_candidate_history()` 读 `interviews/index.md` 构建 `history_summary`
- `key_findings` 永远为空 → `_format_history_summary` 输出"关键发现: 无关键发现"
- `history_summary` 注入 InterviewAgent/EvalAgent Layer 4 后无任何有价值的历史信息
- **候选人长期记忆功能（F6）在正常流程下完全失效**

注：`eval_report.md` 本身写入正确（直接写文件，不依赖 index），通过 `GET /interview/eval?interview_id=xxx` 仍可读取完整报告。`rebuild_index()` 也可恢复（读 eval_report.md 重建）。

**修复建议：**
- 方案一：`save_eval_report()` 若未找到匹配 interview 条目，主动插入新条目（建议立即修复）
- 方案二：`finish_interview()` 写入 index 前检查 `eval_report.md` 是否已存在，若存在则读取填充 eval 字段（可选）

---

## F5：MainAgent 与记忆管理

> Review 日期：2026-06-08 ｜ 共 12 项发现：0 Critical / 0 High / 6 Medium / 6 Low

### [F5-1] MainAgent 三层 system prompt 的内容完整性与更新时机

**功能正确性 — ✅ 三层结构清晰，缓存失效机制正确。**
`_build_system_prompt()` 使用 `_cached_system_prompt` 缓存，Layer 2 更新（`reload_user_memory()`）和 Layer 3 更新（`set_candidate_context()`）都清空缓存，确保下次构建时刷新。

**记忆更新机制 — 🟡 Medium：Layer 3 在 parse_done 后未刷新（已在 F1-3 记录）。**
`parse_done` 副作用未调用 `main_agent.set_candidate_context()`，此处不重复展开。

**实现最优性 — 🟢 Low：Layer 3 resume_content 截断 1500 字符，interview_brief 截断 800 字符。**
```python
# main_agent.py:153-155
if profile.resume_content:
    parts.append(f"简历内容：\n{profile.resume_content[:1500]}")
if interview_brief:
    parts.append(f"面试简报（前800字）：\n{interview_brief[:800]}")
```
面试简报设计上包含 3 个项目 + 3-5 个技能的详细分析，800 字不足以容纳全文。MainAgent 在生成追问建议时参考的简报是截断版本。（EvalAgent 直接使用 `session.interview_brief[:2000]`，稍好但同样有截断风险。）

**实现最优性 — 🟢 Low：Layer 3 不包含 history_summary。**
`set_candidate_context()` 不注入候选人的历史面试记录，MainAgent 在面试准备阶段无法引用"上次面试中候选人暴露了系统设计薄弱"等信息。（InterviewAgent 的 Layer 4 有此信息，MainAgent 不需要，设计可接受，但值得明确文档化。）

---

### [F5-2] manage_user_memory 工具调用时机：主动触发 vs Memory Nudge

**提示词质量 — 🟡 Medium：何时"不应"调用缺少明确约束。**
`_LAYER1_ROLE` 对 `manage_user_memory` 的唯一引导：
> "当面试官提供岗位要求或偏好信息时，主动调用 manage_user_memory 工具保存"

没有说明以下情况不应调用：
- 候选人具体表现（"张三在系统设计上很弱"→不应存入面试官记忆）
- 临时指令（"帮我跳过这道题"→不是持久偏好）
- 会话性回复（"好的，我知道了"→无需记忆）

LLM 可能将候选人相关信息错误写入面试官全局记忆，污染后续所有候选人的评估上下文。

**记忆更新机制 — 🟢 Low：manage_user_memory 调用后 reload 逻辑正确。**
工具调用完成后，`manage_user_memory` 工具会调用 `main_agent.reload_user_memory()`，清空 `_cached_system_prompt`，下一轮构建时 Layer 2 自动刷新。链路正确。

**修复建议：**
- 在 `_LAYER1_ROLE` 补充"不应保存"场景：仅保存跨候选人通用的偏好/岗位要求，候选人个体信息勿存（建议修复）

---

### [F5-3] Memory Nudge 机制设计（触发间隔 10 轮、最近 12 条消息、最多 3 次迭代）

**功能正确性 — 🟡 Medium：recent[-12:] 可能跨候选人，存在上下文污染风险。**
`_background_memory_review()` 取 `self._history[-12:]`（`main_agent.py:411`），但 `_history` 记录所有对话，不区分候选人。若面试官刚切换了候选人，最近 12 条中可能包含候选人 A 的详细信息。nudge LLM 可能将"候选人 A 有 5 年分布式经验"当作"面试官偏好大厂背景候选人"而写入全局记忆。

**实现最优性 — 🟢 Low：nudge 与 main chat 存在潜在竞态。**
nudge task 在 `_chat_lock` 释放后创建（`main_agent.py:397-399`）。若用户快速发送下一条消息，main chat 持锁执行 → 同时 nudge task 也在调用 `manage_user_memory` → `UserMemoryStore._entries` 无锁保护。两者并发修改 entries。单用户工具概率很低，但架构上存在此隐患。

**实现最优性 — 🟢 Low：10 轮 interval 与 12 条 message 窗口不对齐。**
每 10 对话轮次触发一次，但 12 条消息（含 tool_calls + tool_result）在工具密集的会话中只覆盖 4-5 轮。nudge 回顾的历史窗口可能不够完整。

**实现最优性 — 🟢 Low：nudge 异常静默吞掉。**
`except Exception: logger.exception("...failed (ignored)")` 对所有异常一律忽略，包括 API 限流、网络超时等需要感知的错误。nudge 功能静默失效时无告警。

**修复建议：**
- `_NUDGE_SYSTEM` 增加约束：仅参考对话中面试官关于岗位/偏好的表述，忽略候选人具体表现（建议修复）

---

### [F5-4] _history 截断（上限 24 条）对工具调用链完整性的影响

**功能正确性 — 🟡 Medium：截断可能切断 tool_calls / tool_result 消息对。**
```python
def _trim_history(self) -> None:
    if len(self._history) > _HISTORY_LIMIT:
        self._history = self._history[-_HISTORY_LIMIT:]
```
OpenAI API 要求：每条 `role=tool, tool_call_id=X` 消息前必须有对应的 `role=assistant, tool_calls=[{id:X}]` 消息。`_trim_history` 按数量截断，不检查截断点是否落在 tool call pair 中间。

典型危险场景（24 条滚动窗口）：
```
... [21] assistant[tool_calls=tc1]
    [22] tool(tc1)
    [23] assistant[tool_calls=tc2]  ← 截断后第 1 条
    [24] tool(tc2)
```
若第 23 条恰好是截断后的第一条消息，上下文合法。但若：
```
... [21] assistant[tool_calls=tc1]  ← 被截断掉
    [22] tool(tc1)                   ← 截断后第 1 条（孤儿 tool 消息）
    ...
```
孤儿 `tool` 消息会导致 OpenAI API 返回 400 错误。

**修复建议：**
- `_trim_history` 截断后检查首条消息：若为 `role=tool`，继续向前扩展截断点直到找到其前置 `assistant[tool_calls]`（建议修复）
- 或：截断时向后跳过残缺的 tool pair（建议修复）

---

### [F5-5] 切换候选人时不清空对话历史的设计合理性

**功能正确性 — 🟡 Medium：候选人上下文混淆风险。**
`set_candidate_context()` 仅更新 Layer 3 系统提示，不清空 `self._history`。切换候选人后，`_history` 中仍包含前一候选人的讨论：
- LLM 可能将候选人 A 的技术细节作为候选人 B 的背景来回答问题
- 若用户问"他之前有什么问题"，LLM 可能引用前一候选人的内容

设计可能是有意保留连续性（用户可以对比两个候选人），但文档未明确记录此行为。

**修复建议：**
- 在架构文档（`docs/arc/agents.md`）明确记录"切换候选人不清空历史"是有意设计（建议更新文档）
- 可选：提供 `clear_history()` API，让用户主动决定是否清空（可选优化）

---

### [F5-6] UserMemoryStore 的条目化存储格式与字符上限（3000）的合理性

**实现最优性 — 🟢 Low：分隔符保护正确，并发写有隐患。**
`add/replace/remove` 都有分隔符检查和字符上限验证，原子写入（`write_atomic`）保证文件级安全。但 `_entries` list 无 asyncio.Lock 保护，nudge task 与 main chat 若并发写入（见 F5-3）会产生内存级竞态，最终写入结果不确定。

**实现最优性 — 🟢 Low：无去重逻辑，条目可无限堆积相似内容。**
LLM 每次判断"需要保存"时都会 `add` 新条目，若未及时 `replace` 旧条目，类似的岗位要求会堆积多条。3000 字符上限是最后防线，但可能导致"存满 → 后续关键信息无法写入"的静默失败（`add()` 抛 `ValueError`，但 MainAgent 的 tool dispatch 可能不向用户透传此错误）。

**修复建议：**
- 检查 `manage_user_memory` 工具对 `ValueError`（超出字符上限）的错误处理是否向用户可见（建议检查）

---

## F6：候选人长期记忆体系

> Review 日期：2026-06-08 ｜ 共 8 项发现：1 Critical（依赖 F4-5）/ 2 Medium / 5 Low

### [F6-1] history_summary 加载链路（create_session → get_candidate_history → format）

**数据增删改查 — 🔴 Critical（依赖 F4-5 修复）：history_summary 中 key_findings 永远为空。**
`get_candidate_history()` 逻辑正确（`memory_module.py:415-442`）：读 `interviews/index.md`，按 `key_findings` 字段格式化历史摘要。链路本身无缺陷。

但由于 F4-5 的问题，`interviews/index.md` 中所有面试条目的 `key_findings`、`overall_score`、`recommendation` 均为空/NULL。`_format_history_summary` 输出的实际内容形如：

```
候选人 张三 历史面试记录：
1. 2026-05-01 10:00 — 综合评分 未评分，推荐 未推荐
   关键发现: 无关键发现
```

此内容注入 InterviewAgent Layer 4 和 EvalAgent 历史背景后，完全无参考价值，相当于历史记忆功能失效。**F4-5 是此问题的根因，修复 F4-5 即可同步修复本问题。**

---

### [F6-2] key_findings 自动提取逻辑（从 strengths/weaknesses 各取前 2 条）

**实现最优性 — 🟡 Medium：信息提取过于保守，缺少维度评分。**
`save_eval_report()` 的 key_findings 提取（`memory_module.py:879-884`）：
```python
key_findings_parts = []
if report.strengths:
    key_findings_parts.append("优势: " + "; ".join(report.strengths[:2]))  # 仅前2条
if report.weaknesses:
    key_findings_parts.append("不足: " + "; ".join(report.weaknesses[:2]))  # 仅前2条
key_findings = "，".join(key_findings_parts) or report.summary[:100]
```

信息损失：
1. `strengths[2:]` 和 `weaknesses[2:]` 完全丢弃
2. **维度评分缺失**：若候选人"系统设计"仅 2/10，这一关键信号不在 key_findings 中
3. 兜底 `report.summary[:100]` 在 strengths/weaknesses 均为空时产生截断乱语

对比后续面试的 InterviewAgent：Layer 4 注入的历史摘要每条 ~50-100 字，无法有效指导面试官重点考察候选人的薄弱维度。

**修复建议（待 F4-5 修复后有意义）：**
- key_findings 改为包含所有维度评分（格式：`技术深度:3; 系统设计:7; 表达沟通:5`）加 top 1 优劣势（可选优化）

---

### [F6-3] 候选人多次面试后历史记忆的累积与聚合机制

**实现最优性 — 🟢 Low：limit=3 硬编码，无跨面试聚合摘要。**
`get_candidate_history(candidate_id, limit=3)` 默认只取最近 3 次面试（`memory_module.py:416`）。对于面试次数 >3 的候选人，早期面试的关键信息（如"第一次面试时完全不了解分布式系统，但第三次已能清晰讲解"的成长轨迹）不可见。

单用户工具场景下，单个候选人通常 1-3 次面试，当前实现可接受（YAGNI）。但若需要支持长期跟踪候选人，需要跨面试聚合机制。

**实现最优性 — 🟢 Low：interviews/index.md 排序正确（新 → 旧），get_candidate_history 取 [:limit] 即最近 N 条，逻辑正确。**

---

### [F6-4] rounds.jsonl WAL 崩溃恢复机制完整性

**其他 — 🟡 Medium：append_round 缺少 fsync，OS 崩溃可能丢失最后几轮。**
`append_round()` 的写入逻辑（`memory_module.py:492-495`）：
```python
def _append() -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()    # 仅 flush 到 OS buffer
        # 缺: os.fsync(f.fileno())
```
`f.flush()` 把数据从 Python 缓冲区推到 OS 页面缓存，但在 OS 崩溃时页面缓存未落盘的数据会丢失。对于 Python 进程崩溃（常见情况），`flush()` 足够。但机器断电或 OS 崩溃时，最近一轮或几轮可能丢失。

**其他 — 🟡 Medium：WAL 恢复路径（recover_interview_from_wal）不读取 eval_report.md。**
若崩溃发生在 `save_eval_report()` 之后、WAL 归档之前（即 eval_report.md 已写入），`recover_interview_from_wal()` 调用 `finish_interview()`，仍会创建 key_findings 为空的 index 条目，eval 数据丢失于 index（同 F4-5）。`rebuild_index()` 可修复，但需手动触发。

**功能正确性 — ✅ scan_orphan_wal 跳过逻辑正确。**
`if (iv_dir / "transcript.md").exists(): continue` 正确跳过 `finish_interview` 已完成但 WAL 归档失败的残留文件，避免重复恢复。

**功能正确性 — ✅ WAL 归档原子性足够。**
`wal_path.replace(archived)` 在 POSIX 上原子，Windows 上 `Path.replace()` 在目标不存在时同样原子（Win32 MoveFile 语义）。首次归档时 `.archived` 不存在，安全。

**修复建议：**
- 在 `_append()` 中对性能要求低的本地工具场景，可加 `os.fsync(f.fileno())` 提升崩溃安全性（可选优化）
- `recover_interview_from_wal()` 完成后检查 `eval_report.md` 是否存在，若是则读取填充 index（建议修复，依赖 F4-5 修复后更有意义）

---

## F4–F6 发现汇总

| 任务 | 严重程度 | 核心问题 |
|------|---------|---------|
| F4-1 | 🟠 High | EvalAgent 失败时 close_session 永不执行，transcript.md 不写入 |
| F4-2 | 🟡 Medium | EVAL_AGENT_SYSTEM_PROMPT "题目清单"与"面试简报"术语不符 |
| F4-3 | 🟢 Low | Token 估算字符数代理，英文内容触发 map-reduce 阈值偏低 |
| F4-4 | 🟢 Low | close_session 3 次重试设计正确但基本不触发 |
| F4-5 | 🔴 Critical | save_eval_report 在 finish_interview 前执行，interviews/index.md 的 eval 字段永远为空 |
| F5-1 | 🟢 Low | Layer 3 内容截断可接受；parse_done 未刷新 Layer 3（F1-3 已记录） |
| F5-2 | 🟡 Medium | manage_user_memory 触发边界不明确，可能误存候选人信息为全局偏好 |
| F5-3 | 🟡 Medium | nudge[-12:] 跨候选人混合，nudge task 与 main chat 潜在竞态 |
| F5-4 | 🟡 Medium | _trim_history 按数量截断，可能切断 tool_calls/tool_result 消息对 |
| F5-5 | 🟡 Medium | 切换候选人不清空历史，设计未文档化，存在上下文混淆风险 |
| F5-6 | 🟢 Low | UserMemoryStore 无去重，满时写入静默失败；nudge 并发竞态 |
| F6-1 | 🔴 Critical | 依赖 F4-5；key_findings 永远为空，history_summary 失效 |
| F6-2 | 🟡 Medium | key_findings 仅取 strengths/weaknesses 各前 2 条，无维度评分 |
| F6-3 | 🟢 Low | limit=3 硬编码，无跨面试聚合，单用户工具可接受 |
| F6-4 | 🟡 Medium | append_round 缺 fsync；WAL 恢复不读 eval_report.md |

**关键修复优先级（建议立即修复）：**
1. **F4-5** — save_eval_report 在无 index 条目时主动创建，解除执行顺序依赖（同时修复 F6-1）
2. **F4-1** — EvalAgent 失败时确保 close_session 执行（写 transcript，不丢数据）
3. **F4-2 / F3-3** — 统一将 prompt 中"题目清单"改为"面试简报"
4. **F5-4** — _trim_history 增加 tool call pair 完整性检查
