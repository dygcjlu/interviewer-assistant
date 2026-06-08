# Fix Review Findings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 interviewer-assistant 代码库中 Critical/High/Medium 级别的 69 项 Review 发现，提升核心功能正确性与代码健壮性。

**Architecture:** 所有修改均为向后兼容的实现修复，不引入新功能，不改变外部 API 接口。修改集中于 5 个核心文件加 3 个文档文件。

**Tech Stack:** Python 3.12, FastAPI, NiceGUI, SQLite, async/await

---

```yaml
---
change: fix-review-findings
design-doc: docs/superpowers/specs/2026-06-08-fix-review-findings-design.md
base-ref: 4336ceca9e07d1cfaca24d0c3e0404e9358e10da
---
```

---

## Task 1: [T01] memory_module.py — save_eval_report upsert 修复（Critical）

**Files:**
- Modify: `src/storage/memory_module.py`（`save_eval_report` 方法，约 L877–L892）

**问题：** `save_eval_report` 更新 `interviews/index.md` 时，若无匹配条目则静默跳过，eval 分数丢失。

- [ ] **Step 1: 在 `save_eval_report` 的 interviews 循环后，添加"未找到则插入"逻辑**

找到 `src/storage/memory_module.py` 中 `save_eval_report` 方法（约 L877）。  
现有代码循环结束后不更新索引（若无匹配条目），改为：

```python
        found = False
        for iv in interviews:
            if iv.get("interview_id") == report.interview_id:
                iv["overall_score"] = report.overall_score
                iv["recommendation"] = report.recommendation
                iv["key_findings"] = key_findings
                found = True
                break
        if not found:
            interviews.insert(0, {
                "interview_id": report.interview_id,
                "start_time": report.generated_at.isoformat(),
                "end_time": None,
                "stage": "completed",
                "trigger_mode": "auto",
                "overall_score": report.overall_score,
                "recommendation": report.recommendation,
                "key_findings": key_findings,
            })
        self._write_interviews_index(candidate_id, interviews)
```

- [ ] **Step 2: 验证**

运行：`rg "for iv in interviews" src/storage/memory_module.py`  
确认只有 `save_eval_report` 方法中的循环被修改（`finish_interview` 中的循环保持不变）。

- [ ] **Step 3: Commit**

```bash
git add src/storage/memory_module.py
git commit -m "fix: save_eval_report upsert — insert index entry if not found [T01]"
```

---

## Task 2: [T02] routes.py — eval 路由改 finally 确保 close_session（High）

**Files:**
- Modify: `src/web/routes.py`（`get_eval` 函数，约 L417–L465）

**问题：** `eval_agent.handle_request()` 失败时抛出异常，后面的 `close_session` 重试循环不执行。

- [ ] **Step 1: 改造 get_eval，将 close_session 移入 finally**

找到 `src/web/routes.py` 中 `async def get_eval` 函数。当前结构（伪代码）：
```
resp = await eval_agent.handle_request(...)
if not resp.success:
    raise HTTPException(...)
... close_session retry loop ...
return result
```

改为：

```python
@router.get("/interview/eval")
async def get_eval(request: Request, interview_id: str | None = None):
    controller = _controller(request)
    memory = _memory(request)

    if interview_id:
        report = await memory.get_eval_report(interview_id)
        if report is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "评价报告不存在"})
        return {"report": _to_dict(report)}

    session = await controller.get_session() if controller else None
    if session is None:
        raise HTTPException(status_code=409, detail={"code": "no_session", "message": "无活跃会话"})

    eval_resp: Any = None
    eval_error: str | None = None
    try:
        resp = await controller.eval_agent.handle_request(
            AgentRequest(type="generate_eval", payload={}, session=session)
        )
        if not resp.success:
            eval_error = resp.error
        else:
            eval_resp = resp
    except Exception as exc:
        eval_error = str(exc)
        logger.exception("get_eval: eval_agent.handle_request raised")
    finally:
        close_warning: str | None = None
        for attempt in range(3):
            try:
                await controller.close_session()
                close_warning = None
                break
            except Exception as exc:
                logger.warning(
                    "get_eval: close_session attempt %d/3 failed: %s",
                    attempt + 1,
                    exc,
                    exc_info=(attempt == 2),
                )
                close_warning = (
                    "评价已生成，但会话关闭失败（已重试3次）。请刷新页面或重启服务再开始下一次面试。"
                )
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))

    if eval_error is not None:
        raise HTTPException(status_code=500, detail={"code": "eval_error", "message": eval_error})

    save_warning: str | None = eval_resp.data.get("save_warning")
    result: dict[str, Any] = {"report": _to_dict(eval_resp.data["report"])}
    warnings = [w for w in (save_warning, close_warning) if w]
    if warnings:
        result["warning"] = " | ".join(warnings)
    return result
```

- [ ] **Step 2: 验证**

运行：`rg "finally" src/web/routes.py`  
确认 `get_eval` 函数中包含 `finally` 块。

- [ ] **Step 3: Commit**

```bash
git add src/web/routes.py
git commit -m "fix: eval route uses finally to guarantee close_session execution [T02]"
```

---

## Task 3: [T03] dispatch_to_agent.py — parse_done 补调 set_candidate_context（High）

**Files:**
- Modify: `src/tools/dispatch_to_agent.py`（`_apply_side_effects`，约 L99–L132）

**问题：** `parse_done` 分支更新了 `session.candidate` 和 `resume_content`，但未调用 `main_agent.set_candidate_context()`，Layer 3 系统提示保持旧内容。

- [ ] **Step 1: 在 parse_done 分支末尾补调 set_candidate_context**

找到 `_apply_side_effects` 中 `result_type == "parse_done"` 分支末尾（约 L130）。  
在 `session.candidate.resume_content = resume_markdown` 之后（且在 `save_candidate` 成功后），添加：

```python
                session.candidate.resume_content = resume_markdown
            # 更新 MainAgent Layer3：让系统提示包含最新简历内容
            if ctx.main_agent is not None:
                ctx.main_agent.set_candidate_context(
                    session.candidate,
                    resume_content=session.candidate.resume_content,
                    interview_brief=session.candidate.interview_brief if hasattr(session.candidate, "interview_brief") else "",
                )
```

注意：`set_candidate_context` 的签名在 `main_agent.py` 中是 `set_candidate_context(candidate, interview_brief="", resume_content="")`。实际传参时只需传 `candidate` 和可选的 `interview_brief`（MainAgent 内部会从 `candidate.resume_content` 读取内容）。

- [ ] **Step 2: 确认 set_candidate_context 签名**

运行：`rg "def set_candidate_context" src/agents/main_agent.py`  
查看方法签名，按实际签名调整调用。

- [ ] **Step 3: Commit**

```bash
git add src/tools/dispatch_to_agent.py
git commit -m "fix: parse_done calls set_candidate_context to refresh Layer3 prompt [T03]"
```

---

## Task 4: [T04] dispatch_to_agent.py — save_candidate 失败升级为 user_facing 错误（High）

**Files:**
- Modify: `src/tools/dispatch_to_agent.py`（`_apply_side_effects`，约 L126–L133）

**问题：** `save_candidate` 失败时被 `except` 静默吞掉，LLM 返回"解析完成"但候选人档案未落盘。

- [ ] **Step 1: 将 save_candidate 异常改为写入 user_facing 错误**

找到 `parse_done` 分支中的 `save_candidate` 调用（约 L126）：

```python
            try:
                await ctx.memory_module.save_candidate(session.candidate, resume_markdown)
                session.candidate.resume_content = resume_markdown
            except Exception:
                logger.exception("dispatch_to_agent: save_candidate failed")
```

改为：

```python
            try:
                await ctx.memory_module.save_candidate(session.candidate, resume_markdown)
                session.candidate.resume_content = resume_markdown
            except Exception as exc:
                logger.exception("dispatch_to_agent: save_candidate failed")
                result["user_facing"] = f"候选人档案保存失败：{exc}。简历内容未持久化，请重试。"
                return
```

注意：`return` 阻止继续执行 `set_candidate_context`（档案未落盘时不应更新 Layer3）。

- [ ] **Step 2: Commit**

```bash
git add src/tools/dispatch_to_agent.py
git commit -m "fix: save_candidate failure raises user_facing error instead of silent success [T04]"
```

---

## Task 5: [T05] dispatch_to_agent.py — 移除 brief_done 的 start_interview 调用（High）

**Files:**
- Modify: `src/tools/dispatch_to_agent.py`（`_apply_side_effects`，约 L146–L151）
- Modify: `docs/arc/flows.md`（§2 简报生成流程，移除"brief 后自动进入 interviewing"描述）

**UI 依赖确认：** 经检查 `src/web/ui.py`，`stage=interviewing` 用于 trigger_btn 的 enable/disable，但该 stage 由用户点击"开始面试"按钮触发 `/interview/start` → `controller.start_interview()` 设置，移除 `brief_done` 的调用不影响 UI 正常工作。

- [ ] **Step 1: 移除 brief_done 中的 start_interview 调用**

找到 `_apply_side_effects` 中 `result_type == "brief_done"` 分支（约 L134–L154）。  
删除以下代码段（约 L146–L151）：

```python
        profile_path = Path(f"candidates/{cid}/profile.md")
        if ctx.memory_module is not None and profile_path.exists():
            try:
                await ctx.memory_module.start_interview(session)
            except Exception:
                logger.exception("dispatch_to_agent: start_interview failed")
```

保留该分支其余逻辑：`save_brief`、`session.interview_brief = brief_text`、`set_candidate_context`。

- [ ] **Step 2: 更新 docs/arc/flows.md**

找到 `docs/arc/flows.md` 中描述"简报生成后进入 interviewing"的句子，改为说明简报生成后 session 保持 IDLE，由面试官点击"开始面试"触发状态切换。

- [ ] **Step 3: Commit**

```bash
git add src/tools/dispatch_to_agent.py docs/arc/flows.md
git commit -m "fix: remove premature start_interview call from brief_done handler [T05]"
```

---

## Task 6: [T06] interview_controller.py — start_interview 前置条件改为 stage == IDLE（High）

**Files:**
- Modify: `src/agents/interview_controller.py`（`_start_interview_impl`，约 L199–L210）

**问题：** 仅防 INTERVIEWING，对 EVALUATING/COMPLETED 状态下重新 start 无保护。

- [ ] **Step 1: 修改前置检查条件**

找到 `_start_interview_impl`（约 L199），当前代码：

```python
        if self._session.stage == InterviewStage.INTERVIEWING:
            raise SessionError(
                f"面试已在进行中（session_id={self._session.id}），请勿重复开始"
            )
```

改为：

```python
        if self._session.stage != InterviewStage.IDLE:
            raise SessionError(
                f"当前会话状态为 {self._session.stage.value}，无法开始面试（仅 IDLE 状态允许）"
            )
```

- [ ] **Step 2: 验证**

运行：`rg "stage.*INTERVIEWING\|stage.*!=.*IDLE" src/agents/interview_controller.py`  
确认旧的单一检查已被替换。

- [ ] **Step 3: Commit**

```bash
git add src/agents/interview_controller.py
git commit -m "fix: start_interview only allowed from IDLE stage [T06]"
```

---

## Task 7: [T07] audio/manager.py — AudioManager.start() 失败回滚（High）

**Files:**
- Modify: `src/audio/manager.py`（`start` 方法，约 L43–L106）

**问题：** 7 步启动序列中，中途失败不清理已连接的 STT WebSocket 和已创建的 task，导致资源泄漏。

- [ ] **Step 1: 用 try/except 包裹 start()，catch 到异常时逐步回滚**

找到 `async def start(...)` 方法。将步骤 4~7（connect/tasks/capturer/recorder）包入 try/except，异常时回滚：

```python
    async def start(
        self,
        session: InterviewSession,
        ws_sender: Callable[[dict], Awaitable[None]],
        suggestion_trigger: SuggestionTrigger,
        on_round_finalized: Callable[[ConversationRound], Awaitable[None]] | None = None,
    ) -> None:
        """启动音频采集全链路。失败时回滚已分配资源。"""
        # 1. Create TranscriptionManager
        self._transcription_manager = TranscriptionManager(
            session=session,
            ws_sender=ws_sender,
            suggestion_trigger=suggestion_trigger,
            recorder=self._recorder,
            on_round_finalized=on_round_finalized,
        )

        # 2. Create AudioStreamBridge
        self._bridge = AudioStreamBridge(
            candidate_stt=self._candidate_stt,
            interviewer_stt=self._interviewer_stt,
            recorder=self._recorder,
        )

        # 3. Capture event loop
        self._loop = asyncio.get_running_loop()

        def _sync_frame_callback(frame: AudioFrame) -> None:
            try:
                if self._loop is not None and self._bridge is not None:
                    fut = asyncio.run_coroutine_threadsafe(self._bridge.on_frame(frame), self._loop)

                    def _on_done(f: "asyncio.Future") -> None:
                        exc = f.exception() if not f.cancelled() else None
                        if exc is not None:
                            logger.error("AudioManager: on_frame future exception: %s", exc)

                    fut.add_done_callback(_on_done)
            except Exception:
                logger.exception("AudioManager: frame callback error")

        self._capturer.set_on_frame(_sync_frame_callback)

        try:
            # 4. Connect STT engines
            await self._candidate_stt.connect()
            await self._interviewer_stt.connect()

            # 5. Start STT receive loops
            self._candidate_loop_task = self._loop.create_task(
                self._stt_receive_loop(self._candidate_stt)
            )
            self._interviewer_loop_task = self._loop.create_task(
                self._stt_receive_loop(self._interviewer_stt)
            )

            # 6. Start capturer
            await self._capturer.start()

            # 7. Start recording
            await self._recorder.start_recording(session.id, self._recordings_dir)

        except Exception:
            logger.exception("AudioManager: start failed, rolling back")
            await self._rollback_start()
            raise

        logger.info("AudioManager: started for session=%s", session.id)

    async def _rollback_start(self) -> None:
        """回滚 start() 中已分配的资源。"""
        for task in (self._candidate_loop_task, self._interviewer_loop_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._candidate_loop_task = None
        self._interviewer_loop_task = None

        try:
            await self._candidate_stt.close()
        except Exception:
            logger.warning("AudioManager rollback: candidate_stt close failed", exc_info=True)
        try:
            await self._interviewer_stt.close()
        except Exception:
            logger.warning("AudioManager rollback: interviewer_stt close failed", exc_info=True)

        self._transcription_manager = None
        self._bridge = None
```

- [ ] **Step 2: 验证**

运行：`rg "_rollback_start\|rollback" src/audio/manager.py`  
确认回滚方法存在且被调用。

- [ ] **Step 3: Commit**

```bash
git add src/audio/manager.py
git commit -m "fix: AudioManager.start() rolls back allocated resources on failure [T07]"
```

---

## Task 8: [T08] 术语统一 — "题目清单"→"面试简报"（Medium）

**Files:**
- Modify: `src/agents/interview_agent.py`（3 处）
- Modify: `src/agents/prompts.py`（2 处）

**问题：** "题目清单"是旧术语，应统一为"面试简报"。

- [ ] **Step 1: 确认所有出现位置**

运行：`rg "题目清单" src/`  
记录所有匹配行的文件和行号。

- [ ] **Step 2: 替换**

在每个文件中，将"题目清单"替换为"面试简报"。

- [ ] **Step 3: 验证**

运行：`rg "题目清单" src/`  
期望：无结果。

- [ ] **Step 4: Commit**

```bash
git add src/agents/interview_agent.py src/agents/prompts.py
git commit -m "fix: replace deprecated term 题目清单 with 面试简报 [T08]"
```

---

## Task 9: [T09] main_agent.py — _trim_history 保护 tool call pair 完整性（Medium）

**Files:**
- Modify: `src/agents/main_agent.py`（`_trim_history` 方法，约 L401–L403）

**问题：** 按数量截断后首条消息可能为孤儿 `role=tool`，导致 OpenAI API 400。

- [ ] **Step 1: 修改 _trim_history 跳过孤儿 tool 消息**

找到 `_trim_history` 方法：

```python
    def _trim_history(self) -> None:
        if len(self._history) > _HISTORY_LIMIT:
            self._history = self._history[-_HISTORY_LIMIT:]
```

改为：

```python
    def _trim_history(self) -> None:
        if len(self._history) <= _HISTORY_LIMIT:
            return
        trimmed = self._history[-_HISTORY_LIMIT:]
        # 跳过截断后开头的孤儿 tool 消息（其对应的 assistant tool_call 已被截掉）
        while trimmed and trimmed[0].role == "tool":
            trimmed = trimmed[1:]
        self._history = trimmed
```

- [ ] **Step 2: 验证**

运行：`rg "_trim_history\|trim_history" src/agents/main_agent.py`  
确认方法已更新。

- [ ] **Step 3: Commit**

```bash
git add src/agents/main_agent.py
git commit -m "fix: _trim_history skips orphan tool messages after truncation [T09]"
```

---

## Task 10: [T10/T11] prompts.py — 补充记忆约束 prompt（Medium）

**Files:**
- Modify: `src/agents/main_agent.py`（`_LAYER1_ROLE`，约 L81，`_NUDGE_SYSTEM`，约 L84）

**问题：** `_LAYER1_ROLE` 缺少"不应保存"场景说明；`_NUDGE_SYSTEM` 缺少"忽略候选人信息"约束。

- [ ] **Step 1: 补充 _LAYER1_ROLE 中 manage_user_memory 使用约束**

找到 `_LAYER1_ROLE` 字符串（约 L51–L82）。在"当面试官提供岗位要求或偏好信息时，主动调用 manage_user_memory 工具保存"之前，添加约束说明：

```python
- 当面试官提供岗位要求或偏好信息时，主动调用 manage_user_memory 工具保存
- **不应保存**：候选人个人信息（姓名、简历内容、面试表现、回答质量等）——这些属于候选人档案，由 dispatch_to_agent 持久化管理
```

- [ ] **Step 2: 补充 _NUDGE_SYSTEM 中候选人信息约束**

找到 `_NUDGE_SYSTEM` 字符串（约 L84–L90）。在"注意："列表中添加：

```python
- 只保存面试官明确表达的、具有长期参考价值的信息（岗位要求、技术栈偏好、面试风格等）
- **忽略候选人具体表现**：候选人的回答内容、评价、能力判断等不应保存到面试官记忆
- 若已有相似条目，使用 replace 更新而非重复 add
- 若无值得保存的内容，不要调用任何工具，直接结束
```

- [ ] **Step 3: Commit**

```bash
git add src/agents/main_agent.py
git commit -m "fix: add memory constraint prompts to prevent candidate info pollution [T10/T11]"
```

---

## Task 11: [T12] prompts.py — age 字段改为可选（Medium）

**Files:**
- Modify: `src/agents/prompts.py`（约 L32）

**问题：** `RESUME_AGENT_SYSTEM_PROMPT` 将 `age` 列为必填字段，简历中无年龄时 LLM 会编造。

- [ ] **Step 1: 修改 profile 字段说明**

找到约 L32：

```
profile 必须包含：name、email、phone、age、skills、years_of_experience、current_position
```

改为：

```
profile 必须包含：name、skills、years_of_experience、current_position
可选字段（有则填，无则省略，不得编造）：email、phone、age
```

- [ ] **Step 2: 验证**

运行：`rg "age" src/agents/prompts.py`  
确认 age 不再标注为必填。

- [ ] **Step 3: Commit**

```bash
git add src/agents/prompts.py
git commit -m "fix: make age field optional in resume agent profile schema [T12]"
```

---

## Task 12: [T13] routes.py — /candidates 接口返回真实总数（Medium）

**Files:**
- Modify: `src/web/routes.py`（`list_candidates`，约 L470–L483）
- Modify: `src/storage/memory_module.py`（添加 `count_candidates` 方法）

**问题：** `total` 使用截断后的 `len(candidates)`，最多为 `limit+offset`，不是真实总数。

- [ ] **Step 1: 在 MemoryModule 添加 count_candidates 方法**

在 `src/storage/memory_module.py` 中 `search_candidates` 方法之后，添加：

```python
    async def count_candidates(self, keyword: str = "") -> int:
        """返回符合关键词筛选的候选人总数（不受 limit/offset 影响）。"""
        candidates = self._read_candidates_index()
        if keyword:
            candidates = [c for c in candidates if keyword.lower() in (c.get("name") or "").lower()]
        return len(candidates)
```

- [ ] **Step 2: 修改 routes.py list_candidates**

找到 `list_candidates`（约 L470），当前代码：

```python
    candidates = await memory.search_candidates(keyword=keyword, limit=limit + offset)
    paged = candidates[offset : offset + limit]
    return {
        "candidates": [_to_dict(c) for c in paged],
        "total": len(candidates),
    }
```

改为：

```python
    total = await memory.count_candidates(keyword=keyword)
    candidates = await memory.search_candidates(keyword=keyword, limit=limit, offset=offset)
    return {
        "candidates": [_to_dict(c) for c in candidates],
        "total": total,
    }
```

- [ ] **Step 3: 验证**

运行：`rg "count_candidates\|total" src/web/routes.py src/storage/memory_module.py`  
确认 `total` 来源于 `count_candidates`。

- [ ] **Step 4: Commit**

```bash
git add src/web/routes.py src/storage/memory_module.py
git commit -m "fix: /candidates returns true total count via count_candidates [T13]"
```

---

## Task 13: [T14] routes.py — 候选人去重改为解析后按真实姓名判断（Medium）

**Files:**
- Modify: `src/web/routes.py`（`upload_resume`，约 L173–L185）

**问题：** 上传时用文件名 stem 做去重检查，但简历解析是异步的，文件名与实际姓名可能不同。

**设计决策：** 移除上传时基于文件名的阻塞式去重（它检查的是 stem，不是真实姓名），改为后置警告方式——在 `dispatch_to_agent` `parse_done` 分支中，检查解析出的真实姓名是否已存在，若存在则在 result 中写入 `duplicate_warning` 字段。

- [ ] **Step 1: 移除 upload_resume 中的文件名去重检查**

找到约 L173–L185：

```python
    # 去重检查
    if not candidate_id and not overwrite:
        existing = await memory.get_candidate_by_name(safe_stem)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "duplicate_candidate",
                    "message": f"候选人「{safe_stem}」已存在，请确认是否覆盖",
                    "existing_candidate_id": existing.id,
                    "existing_candidate_name": existing.name,
                },
            )
```

删除这段代码（去掉去重 HTTP 异常）。

- [ ] **Step 2: 在 dispatch_to_agent parse_done 分支添加重名警告**

找到 `_apply_side_effects` 的 `parse_done` 分支，在 `save_candidate` 调用成功之后，添加重名检查：

```python
                await ctx.memory_module.save_candidate(session.candidate, resume_markdown)
                session.candidate.resume_content = resume_markdown
                # 检查是否已存在同名候选人（解析出真实姓名后再去重）
                real_name = session.candidate.name
                if real_name and ctx.memory_module is not None:
                    existing = await ctx.memory_module.get_candidate_by_name(real_name)
                    if existing is not None and existing.id != session.candidate.id:
                        result["duplicate_warning"] = (
                            f"候选人「{real_name}」已存在（ID: {existing.id}），"
                            f"当前解析结果已另存为新档案。如需覆盖，请手动删除旧档案。"
                        )
```

- [ ] **Step 3: Commit**

```bash
git add src/web/routes.py src/tools/dispatch_to_agent.py
git commit -m "fix: move candidate dedup to post-parse check using real name [T14]"
```

---

## Task 14: [T15] interview_controller.py — 未知 candidate_id 明确 404（Medium）

**Files:**
- Modify: `src/agents/interview_controller.py`（`_create_session_impl`，约 L93–L134）

**问题：** `candidate_id` 不存在时静默创建空档案，而非明确报错。

- [ ] **Step 1: 添加 candidate_id 不存在时的明确错误**

找到 `_create_session_impl`（约 L93），当前代码：

```python
        if candidate_id:
            existing = await self._memory.get_candidate(candidate_id)
            if existing is not None:
                candidate = existing
                ...
            else:
                candidate = CandidateProfile(id=candidate_id, name="")
```

改为：

```python
        if candidate_id:
            existing = await self._memory.get_candidate(candidate_id)
            if existing is not None:
                candidate = existing
                ...
            else:
                raise SessionError(f"候选人不存在：{candidate_id}")
```

- [ ] **Step 2: 确认 routes.py 调用方处理 SessionError**

运行：`rg "create_session\|SessionError" src/web/routes.py`  
确认调用 `create_session` 的地方（`select_candidate`、`upload_resume`）会将 `SessionError` 转为适当的 HTTP 错误。  
注意：`select_candidate` 已在调用前检查 `candidate is None`（L104），`upload_resume` 已在后续调用 `create_session(candidate_id)` 前检查不到 candidate_id 时传 `None`。检查这些路径是否需要 try/except。

- [ ] **Step 3: 在 routes.py 中保护 create_session 调用**

找到 `src/web/routes.py` 中调用 `controller.create_session(body.candidate_id)` 的地方（`select_candidate` 约 L111），添加 try/except：

```python
            try:
                session = await controller.create_session(body.candidate_id)
            except SessionError as exc:
                raise _session_err(exc)
```

- [ ] **Step 4: Commit**

```bash
git add src/agents/interview_controller.py src/web/routes.py
git commit -m "fix: create_session raises SessionError for unknown candidate_id [T15]"
```

---

## Task 15: [T16-T18] 文档更新

**Files:**
- Modify: `docs/arc/agents.md`（T16）
- Modify: `docs/arc/flows.md`（T17，已在 Task 5 部分更新，补充剩余内容）
- Modify: `docs/feature-review-plan.md`（T18）

- [ ] **Step 1: docs/arc/agents.md — 记录"切换候选人不清空历史"为有意设计 [T16]**

找到 `docs/arc/agents.md` 中 MainAgent 相关段落，添加说明：

> **有意设计**：切换候选人（`/candidate/select` 或 `create_session`）时不清空 MainAgent 的对话历史（`_history`）。理由：面试官可能在同一工作流中连续讨论多位候选人，保留历史可维持对话连贯性；若需要完全隔离，可刷新页面或重启会话。

- [ ] **Step 2: docs/arc/flows.md — 更新简报生成流程说明 [T17]**

确认 Task 5（T05）中已更新"brief 后自动进入 interviewing"的描述。若未完整，补充：简报生成后 stage 保持 IDLE，面试官点击"开始面试"触发 `/interview/start` → stage=INTERVIEWING。

- [ ] **Step 3: docs/feature-review-plan.md — 修正 ContextManager 压缩对评价影响的描述 [T18]**

找到相关段落，修正：ContextManager 压缩只影响 InterviewAgent 的实时对话上下文（用于追问建议），EvalAgent 使用 `transcript.md` 完整转写记录生成评价，不受 ContextManager 压缩影响。

- [ ] **Step 4: Commit**

```bash
git add docs/arc/agents.md docs/arc/flows.md docs/feature-review-plan.md
git commit -m "docs: clarify intentional design choices and correct misconceptions [T16-T18]"
```

---

## 完成验证

- [ ] 运行应用确保启动无报错：`python -m src.main`（Ctrl+C 停止即可）
- [ ] 运行 rg 验证关键修复：

```bash
# T01: upsert 逻辑
rg "if not found" src/storage/memory_module.py

# T08: 术语替换
rg "题目清单" src/  # 期望：无结果

# T09: 孤儿保护
rg "role.*tool" src/agents/main_agent.py

# T12: age 可选
rg "age" src/agents/prompts.py  # 不应包含"必须"
```
