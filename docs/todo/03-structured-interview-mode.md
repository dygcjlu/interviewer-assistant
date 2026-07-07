# #3 结构化面试模式

## 目标

面试简报生成后，同时生成有序问题清单（含预期考察点），面试过程中在侧边栏实时展示进度，帮助面试官确保关键问题都被覆盖。

## 范围

- 面试简报生成时，同步生成结构化问题清单（问题 + 预期考察点）
- 主界面增加右侧侧边栏，常驻展示问题清单及覆盖状态
- 覆盖判定：LLM 自动分析转写内容标记已覆盖问题，面试官可手动纠正（勾选/取消）
- 未覆盖问题在侧边栏高亮提示

## 数据模型

每个问题条目包含：
- `id`：唯一标识
- `question`：问题文本
- `focus`：预期考察点
- `covered`：是否已覆盖（bool）
- `covered_by`：自动/手动

## 验收条件

> 实际进度总述：该功能的数据模型、持久化、生成、覆盖判定与 EvalReport 集成均已实现，比文档标题暗示的"待办"状态推进得多；唯一明显偏离设计的是第 2 项（未做成常驻侧边栏，而是做成可切换的标签页）。

- [x] 面试简报生成后，问题清单自动生成并持久化到候选人档案
  - 实现：`src/tools/dispatch_to_agent.py` 中 `brief_done` 分支（约第 193 行）调用 `asyncio.create_task(_generate_questions_from_brief(cid, brief_text))`；该函数（约第 217–280 行）调用 LLM 生成 5–10 个 `{question, focus}`，再通过 `ctx.memory_module.save_questions(candidate_id, questions)` 持久化。存储层实现在 `src/storage/memory_module.py`：`_questions_path()`（第 852–853 行，写入 `candidates/{id}/questions.json`）、`save_questions()`（第 855–864 行）、`get_questions()`（第 866–877 行）、`update_question_coverage()`（第 879–894 行）。数据模型见 `src/models/question.py`（`InterviewQuestion`：`id`/`question`/`focus`/`covered`/`covered_by`）。
- [ ] 主界面右侧侧边栏展示问题清单，每条显示问题文本、考察点、覆盖状态
  - 实际进度：问题文本、考察点、覆盖状态本身均已实现并正确展示（`src/web/ui.py` `_render_questions()`，约第 816–874 行：显示 `question`/`focus`/`covered`/`covered_by`，并有整体覆盖进度统计）。但展示位置不是设计要求的"常驻侧边栏"，而是右侧面板 5 个标签页之一（`tab_qs = ui.tab("问题", ...)`，`src/web/ui.py` 约第 214、231–234 行），需要面试官手动切换到"问题"标签才能看到，与"常驻展示"的验收描述有出入。
- [x] 每轮转写后，LLM 自动判断哪些问题已被覆盖，更新覆盖状态
  - 实现：`src/web/ui.py` 约第 701–706 行，每次收到 `session_snapshot` 且轮次数增加时 `asyncio.create_task(_check_question_coverage(...))`；该函数（约第 774–794 行）调用后端 `POST /api/interview/questions/check-coverage`（`src/web/routes.py` 约第 667–727 行，内部用 LLM 分析本轮对话并返回已覆盖问题 ID 列表）。另有等价的后端内部触发路径 `_auto_check_coverage`（`src/web/routes.py` 约第 609–664 行），并被单元测试 `tests/unit/test_auto_coverage_check.py` 覆盖。
- [ ] 面试官可手动勾选/取消勾选覆盖状态，手动操作优先级高于自动判断
  - 实际进度：手动勾选/取消勾选本身已实现（`PATCH /api/interview/questions/{question_id}`，`src/web/routes.py` 约第 592–606 行，前端 checkbox 见 `src/web/ui.py` 约第 851–864 行，均支持 `covered=true/false` 双向切换）。但"手动操作优先级高于自动判断"未被严格保证：自动检测逻辑（`_auto_check_coverage`/`check_question_coverage`）只是简单地跳过当前 `covered=True` 的问题（`uncovered = [q for q in questions if not q.get("covered")]`），并不会区分某问题此前是被"手动取消勾选"还是"从未覆盖"。因此如果面试官手动取消勾选一个此前已覆盖的问题，后续一轮自动检测仍可能将其重新判定为已覆盖（标记为 `covered_by="auto"`），实质上覆盖了面试官的手动操作。代码和测试中都未见针对这一优先级规则的显式保护或用例。
- [x] 未覆盖问题有视觉区分（如不同颜色或图标）
  - 实现：`src/web/ui.py` `_render_questions()` 中已覆盖问题文本加删除线 + 绿色（`text-green-8`），未覆盖为默认灰黑色（`text-grey-9`），并附"✓"标记及"[自动/手动]"角标。
- [x] 面试结束后，问题覆盖率统计写入 EvalReport（已覆盖 N/总计 M）
  - 实现：`src/agents/eval_agent.py` 约第 102–110 行，`_generate_eval` 中读取 `self._memory_module.get_questions(...)` 计算 `coverage_text = f"已覆盖 {covered_count}/{total_questions}"`，并在约第 200 行写入 `EvalReport(question_coverage=coverage_text, ...)`；`EvalReport.question_coverage` 字段定义见 `src/models/evaluation.py` 第 27 行。单元测试 `tests/unit/test_eval_agent_coverage.py` 覆盖此路径。
