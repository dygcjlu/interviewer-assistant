---
comet_change: fix-review-findings
role: technical-design
canonical_spec: openspec
archived-with: 2026-06-09-fix-review-findings
status: final
---

# Fix Review Findings — 技术设计文档

## 背景

本次 change 基于 F1–F6 全功能 Review 输出的 69 项发现，集中修复 Critical/High bug，选择性修复高价值 Medium 问题。所有修复均为实现层变更，不改变外部 API 接口或行为语义。

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T01 — save_eval_report upsert（F4-5 Critical）

**根因**：`save_eval_report` 调用时 `finish_interview` 尚未执行，`interviews/index.md` 无本次面试条目，评分数据更新循环直接 break，eval 字段永远 NULL。

**修复方案**：在 `save_eval_report`（`src/storage/memory_module.py`）的 index 更新循环后，若未找到匹配条目，从 `session.json` 读取基础字段并 insert 新条目（含 eval 字段）：

```python
if not found:
    session_json_path = self._session_json_path(candidate_id, report.interview_id)
    try:
        sd = json.loads(session_json_path.read_text(encoding="utf-8"))
    except Exception:
        sd = {}
    interviews.insert(0, {
        "interview_id": report.interview_id,
        "start_time": sd.get("start_time", datetime.now().isoformat()),
        "end_time": sd.get("end_time"),
        "stage": sd.get("stage", "interviewing"),
        "trigger_mode": sd.get("trigger_mode", "auto"),
        "overall_score": report.overall_score,
        "recommendation": report.recommendation,
        "key_findings": key_findings,
    })
```

`finish_interview` 后续执行时检测到条目已存在（`existing_idx >= 0`），走"保留 eval 字段"路径，幂等安全。

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T02 — eval 路由 finally 确保 close_session（F4-1 High）

**根因**：`routes.py` eval 路由中 `if not resp.success: raise HTTPException` 在 `close_session` 重试块之前，eval 失败时 session 永久僵死。

**修复方案**：将 success 检查移到 `close_session` 重试块**之后**——先无论如何执行 close，再判断结果：

```python
resp = await controller.eval_agent.handle_request(...)
save_warning = resp.data.get("save_warning") if resp.success else None

# close_session 无论 eval 成功与否都执行
close_warning = None
for attempt in range(3):
    try:
        await controller.close_session()
        break
    except Exception as exc:
        ...  # 同现有重试逻辑

# eval 失败时 session 已关闭，再 raise
if not resp.success:
    raise HTTPException(500, ...)
```

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T03 — parse_done 补调 set_candidate_context（F1-3 High）

**根因**：`dispatch_to_agent.py` 的 `parse_done` 分支更新 `session.candidate` 后未调用 `main_agent.set_candidate_context()`，MainAgent Layer 3 保持旧/空内容，阶段一 LLM 拿不到完整简历。

**修复方案**：在 `parse_done` 分支 `save_candidate` 成功后追加：

```python
if ctx.main_agent is not None:
    ctx.main_agent.set_candidate_context(
        session.candidate, resume_content=session.candidate.resume_content
    )
```

参照已有 `brief_done` 分支（L154）的调用方式。

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T04 — save_candidate 失败升级为 user_facing 错误（F1-1 High）

**根因**：`parse_done` 分支的 `save_candidate` 调用被 `try/except` 吞掉，LLM 返回"解析完成"但文件未落盘。

**修复方案**：失败时注入 `user_facing` 字段并 return，不再静默成功：

```python
try:
    await ctx.memory_module.save_candidate(session.candidate, resume_markdown)
    session.candidate.resume_content = resume_markdown
except Exception:
    logger.exception("dispatch_to_agent: save_candidate failed")
    result["user_facing"] = "候选人档案写入失败，请重试"
    return
```

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T05 — 移除 brief_done 中的 start_interview 调用（F1-4 High）

**根因**：`dispatch_to_agent.py` L146-151 在简报生成后立即调用 `memory_module.start_interview()`，写 `stage=interviewing` 到 `session.json`，但面试未真正开始，产生孤儿 session 目录。

**已确认 UI 安全**：`ui.py:401` 的 `state["stage"]` 是前端本地状态，仅在用户点击"开始面试" API 成功后更新，不依赖后端持久化层的 session.json。

**修复方案**：删除 `brief_done` 分支中以下代码块：

```python
# 删除这段（L146-151）
profile_path = Path(f"candidates/{cid}/profile.md")
if ctx.memory_module is not None and profile_path.exists():
    try:
        await ctx.memory_module.start_interview(session)
    except Exception:
        logger.exception("dispatch_to_agent: start_interview failed")
```

`stage=interviewing` 唯一由 `InterviewController.start_interview()` 写入，与 `docs/arc/flows.md` 设计一致。

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T06 — start_interview 前置条件收严（F2-1 High）

**根因**：`_start_interview_impl` 只防 `INTERVIEWING`，对 `EVALUATING`/`COMPLETED` 无保护，可将评价中的会话拉回面试。

**修复方案**（`src/agents/interview_controller.py`）：

```python
# 修改前
if self._session.stage == InterviewStage.INTERVIEWING:
    raise SessionError(...)

# 修改后
if self._session.stage != InterviewStage.IDLE:
    raise SessionError(
        f"面试无法开始，当前状态为 {self._session.stage.value}，需为 idle"
    )
```

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T07 — AudioManager.start() 失败回滚（F2-2 High）

**根因**：7 步启动序列中途失败不清理已连接的 STT WebSocket 和已创建的 task，每次失败启动都泄漏资源。

**修复方案**（`src/audio/manager.py`）：在步骤 4-7 外套 `try/except`，catch 时逐步回滚：

```python
try:
    await self._candidate_stt.connect()
    await self._interviewer_stt.connect()
    self._candidate_loop_task = self._loop.create_task(...)
    self._interviewer_loop_task = self._loop.create_task(...)
    await self._capturer.start()
    await self._recorder.start_recording(session.id, self._recordings_dir)
except Exception:
    for task in (self._candidate_loop_task, self._interviewer_loop_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    self._candidate_loop_task = None
    self._interviewer_loop_task = None
    for stt in (self._candidate_stt, self._interviewer_stt):
        try:
            await stt.close()
        except Exception:
            pass
    self._transcription_manager = None
    self._bridge = None
    raise
```

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T08 — 术语替换（F3-3/F4-2 Medium）

全局替换 4 处（`interview_agent.py` 3处 + `prompts.py` 1处注释）：`"题目清单"` → `"面试简报"`。

使用 `rg "题目清单" src/` 定位后 StrReplace 逐处替换。

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T09 — _trim_history 保护 tool call pair（F5-4 Medium）

**根因**：按数量截断可能使 `self._history[0].role == "tool"`，导致 OpenAI API 400。

**修复方案**（`src/agents/main_agent.py`）：

```python
def _trim_history(self) -> None:
    if len(self._history) > _HISTORY_LIMIT:
        self._history = self._history[-_HISTORY_LIMIT:]
    while self._history and self._history[0].role == "tool":
        self._history.pop(0)
```

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T10/T11 — 补充记忆约束 prompt（F5-2/F5-3 Medium）

**修复方案**（`src/agents/main_agent.py`）：

- `_LAYER1_ROLE` 的 `manage_user_memory` 引导段追加：「不应保存候选人具体表现、本次面试对话内容、或面试官对特定候选人的评价」
- `_NUDGE_SYSTEM` 追加：「只关注面试官对岗位要求和面试偏好的描述，忽略候选人具体表现」

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T12 — age 字段改可选（F1-2 Medium）

**修复方案**（`src/agents/prompts.py`）：在 profile schema 的 `age` 字段说明中改为：「年龄（可选，无则省略，勿编造）」

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T13 — /candidates total 修复（F1-5a Medium）

**修复方案**：

1. `src/storage/memory_module.py` 新增：
```python
def count_candidates(self, keyword: str = "") -> int:
    candidates = self._read_candidates_index()
    if keyword:
        candidates = [c for c in candidates if keyword.lower() in (c.get("name") or "").lower()]
    return len(candidates)
```

2. `src/web/routes.py` 改为：
```python
total = memory.count_candidates(keyword=keyword)
candidates = await memory.search_candidates(keyword=keyword, limit=limit, offset=offset)
```

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T14 — 去重策略改进（F1-5b Medium）

**决策**：移除上传时基于文件名的去重检查（几乎永远不匹配），改为 parse_done 后基于真实姓名的软警告。

**修复方案**：

- `src/web/routes.py`：删除上传时 `get_candidate_by_name(safe_stem)` 去重块（L174-185）
- `src/tools/dispatch_to_agent.py`：parse_done 副作用中，`save_candidate` 成功后检查同名：

```python
existing_same_name = await ctx.memory_module.get_candidate_by_name(session.candidate.name)
if existing_same_name is not None and existing_same_name.id != session.candidate.id:
    result["warning"] = (
        f"候选人「{session.candidate.name}」已存在（ID: {existing_same_name.id}），"
        f"当前已另行创建新档案"
    )
```

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T15 — create_session 未知 candidate_id 明确报错（F2-3 Medium）

**修复方案**（`src/agents/interview_controller.py`）：

```python
existing = await self._memory.get_candidate(candidate_id)
if existing is None:
    raise SessionError(f"候选人 {candidate_id!r} 不存在，请检查 ID 是否正确")
```

routes.py 已有 `except SessionError → 409`，此处 raise 会被正确处理为 409 响应。

archived-with: 2026-06-09-fix-review-findings
status: final
---

## T16–T18 — 文档更新

- **T16**：`docs/arc/agents.md` 补充"切换候选人不清空对话历史为有意设计"说明（跨候选人上下文有助于面试官连贯工作）
- **T17**：`docs/arc/flows.md §2` 修正"brief 后自动进入 interviewing"的误导性描述
- **T18**：`docs/review-findings.md` F3-5 描述修正：ContextManager 压缩仅影响实时建议上下文，EvalAgent 直接使用完整 `session.rounds`，评价不受影响

archived-with: 2026-06-09-fix-review-findings
status: final
---

## 风险与权衡

| 任务 | 风险 | 缓解 |
|---|---|---|
| T05 移除 brief_done start_interview | 低：UI 不依赖持久化 stage | 已确认 ui.py 逻辑 |
| T02 eval 失败后仍 close_session | transcript 含部分/无 eval 数据 | 期望行为（保数据完整） |
| T09 孤儿 tool 消息删除 | 丢失历史 tool result | API 400 是必现问题，两害取轻 |
| T14 移除上传去重 | 重复候选人档案风险 | parse_done 后软警告兜底 |
