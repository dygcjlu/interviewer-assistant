# Progress Ledger

Change: opensource-optimization-rollout
Plan: docs/superpowers/plans/2026-07-06-opensource-optimization-rollout.md
Started: 2026-07-06T23:59:00+08:00

## Completed Tasks

Task 1.1: complete (commits 5e2891d..99173d5, batch review with 1.2/1.3 clean after 1 fix round)
Task 1.2: complete (commits 99173d5..2712ef1, fix 4d32549 for dead pytest.ini_options, batch review clean)
Task 1.3: complete (commits 2712ef1..28008d2, batch review clean, 485/485 tests no regression)
Task 1.4: complete (commit 69dcc2c, batch review with 1.5/1.6 Approved; accepted Important: bundled real `ui.py` `_dispatch` qs_col F821 fix without regression test — ui.py has 0% coverage, fix verified safe/correct by cross-check, deferred)
Task 1.5: complete (commit 18ff105, batch review with 1.4/1.6 Approved; coverage 58.48% < 60% gate is expected interim state, tracked by Task 6.5/9.1)
Task 1.6: complete (commit b296b74, batch review with 1.4/1.5 Approved, RED/GREEN evidence solid)
Task 1.7: complete (commit ff9b6ac, batch review with 1.8/1.9; found structured-interview-mode far more complete than docs implied, plus a real manual-uncheck-priority defect; fix round 14e0b6b corrected 3 overstated checkboxes, re-review Approved)
Task 1.8: complete (commit 39d9914, batch review Approved, all 8 CHANGELOG entries verified against real commits)
Task 1.9: complete (commit c4dfd82, batch review Approved, verbatim match to brief)
Task 1.10: complete (commit 0d6b299, batch review with 1.11/1.12 Approved, verbatim match to brief)
Task 1.11: complete (commit fe6c1bd, batch review Approved; verified correction "停止面试"->"结束面试" confirmed accurate against src/web/ui.py:142)
Task 1.12: complete (commit 50e26e8, batch review Approved, README insertion minimal/precise)

## Phase 1 complete (Tasks 1.1-1.12, commit b22db7c checks off final batch)

Task 2.1: complete (commit e15c882, batch review with 2.2; 1 fix round for weak single-call test assertion, fix commit 5a5981a, re-review Approved)
Task 2.2: complete (commit 90704eb, batch review with 2.1 Approved; test correctly used assert_called_once() + message-list-length checks from the start)
Task 2.3: complete (commit c53bb7a, batch review with 2.4 Approved; real-tiktoken regression proves compression trigger timing unaffected, no constants adjusted)
Task 2.4: complete (commit 2ae077b, batch review with 2.3 Approved; discovered new exact estimate is HIGHER than old //3 heuristic for CN-heavy text, opposite of design doc's original assumption — asserted the empirical direction, judged correct by reviewer; design doc line 43 stale-assumption correction deferred as accepted Minor to closing tasks 9.2/9.3)

## Phase 2 — Token 精确计数 sub-phase complete (Tasks 2.1-2.4)

Task 3.1: complete (commit 79c8e30, batch review with 3.2/3.3/3.4 Approved)
Task 3.2: complete (commit 79c8e30, batch review with 3.1/3.3/3.4 Approved)
Task 3.3: complete (commit 050f76f, batch review with 3.1/3.2/3.4 Approved; found and fixed a real self-cancellation race bug in generate_suggestion()'s cancel-previous-stream preamble, independently reproduced and verified correct by reviewer)
Task 3.4: complete (commit 050f76f, batch review with 3.1-3.3 Approved; two tests covering direct-call and _runner-wrapped cancellation paths, 5x repeat runs non-flaky)

Task 3.5: complete (E2E browser 2026-07-07; 5 suggestion_delta WS events; report .superpowers/sdd/briefs/task-3.5-report.md)

Task 4.6: complete (E2E browser 2026-07-07; fixed ui.py dedup dialog parent slot bug; cancel branch verified; report .superpowers/sdd/briefs/task-4.6-report.md)

## Phase 2 fully complete (including blocked e2e tasks 3.5, 4.6)

## Build phase complete — all tasks.md items checked; final gate: ruff clean + 548 passed + 62.43% coverage

Task 4.1: complete (design/investigation task, no code commit; report .superpowers/sdd/task-4.1-report.md; corrected brief's wrong assumption that dispatch_to_agent.py is an SSE generator, redesigned event short-circuit to live in main_agent.py tool loop; identified premature session.candidate mutation risk and designed dataclasses.replace-based fix)
Task 4.2: complete (commit 1040a5b, batch review with 4.4 Approved after 1 fix round; fix commit a2654e3 for shared pending-object mutation bug in resolve_duplicate's overwrite branch, re-review Approved)
Task 4.4: complete (commit 1040a5b + fix a2654e3, batch review with 4.2 — see above; same fix round covers both)

Task 4.3: complete (commit 06e8921, individual review Approved; corrected implementer's brief in-flight — threaded new `state` param through `_chat_stream`/`_trigger_parse` since plan's sample code assumed access that didn't exist; removed dead `_confirm_overwrite_dialog`/`_conflict` upload-time 409 path; 2 accepted Minor findings, neither blocking)

## Phase 2 — 候选人去重改为按真实姓名 sub-phase: Tasks 4.1, 4.2, 4.3, 4.4 complete (backend + frontend); Task 4.6 (e2e browser, likely blocked same as 3.5) still pending; Task 4.5 (tests) backend portion covered via TDD in 4.2/4.4, frontend portion has no dedicated automated test (ui.py near-zero coverage is a pre-existing accepted state) — real UX validation deferred to Task 4.6

Task 5.1: complete (commit 2ed6301, individual review Approved; CJK assertions genuinely executed — not skipped — on this Windows machine via SimHei font; pymupdf already a declared dependency, no new deps added)

## Phase 2 — PDF 导出中文渲染测试 sub-phase complete (Task 5.1). Phase 2 as a whole now only missing the two blocked e2e browser tasks (3.5, 4.6).

Task 6.1: complete (commit 6c721ab, individual review Approved; verified real {"type":"tool_call"} timing precedes dispatch() resolution even on error short-circuit path, adjusted assertions to match; skipped duplicating duplicate_candidate coverage already in test_agents.py)
Task 6.2: complete (commit 12c9168, batch review with 6.3 Approved; reviewer independently traced slice-index arithmetic for orphan-tool-at-head scenario against real _trim_history logic, confirmed exact)
Task 6.3: complete (commit 12c9168, batch review with 6.2 Approved; deterministic asyncio.sleep(0) task-scheduling assertion verified non-flaky across 8 consecutive reviewer re-runs; pre-set _turns_since_nudge shortcut confirmed faithful to real cumulative logic)
Task 6.4: complete (commit efe01b5, individual review Approved, zero findings; scope narrowed from brief's 5 scenarios to the 2 genuinely uncovered ones after confirming 3 already had test coverage elsewhere; brief's assumed test_routes.py file never existed, added to test_interview_lifecycle.py instead)
Task 6.5: complete (commit 5edda17, individual review Approved, zero Critical/Important; routes.py already 71% with no changes needed, main_agent.py raised 67% -> 74% via 10 targeted unit tests, reviewer verified each was a meaningful assertion not padding; full suite 530 passed, ruff clean)

## Phase 3 — MainAgent + routes 测试补齐 sub-phase complete (Tasks 6.1-6.5)

Task 7.1: complete (no code commit, verification-only; baseline recorded: 530 passed, 16.73s, 0 failures — reference snapshot for Task 7.6 post-split regression check)
Task 7.2: complete (commit 6ba12ff, batch review with 7.3/7.4/7.5 Approved, see 7.5 note)
Task 7.3: complete (commit 6c1a65c, batch review with 7.2/7.4/7.5 Approved, see 7.5 note)
Task 7.4: complete (commit 71fb285, batch review with 7.2/7.3/7.5 Approved, see 7.5 note)
Task 7.5: complete (Step D found zero gaps, no 4th commit needed; independent review Approved, zero Critical/Important; 2 accepted Minor — asymmetric private-method access in rebuild_index, missing comment on re-export imports; InterviewStore/EvalStore cross-dependency resolved via Facade-level dataclasses.replace assembly rather than circular set_eval_store injection; reviewer independently re-ran full suite 530 passed + ruff clean, spot-checked WAL crash-recovery trio, get_candidate_history placement, eval orphan-fallback path, and all historical import compat sites)
Task 7.6: complete (no code commit, verification-only; 530 passed matching Task 7.1 baseline exactly, coverage gate --cov-fail-under=60 passed at 62.42%)

## Phase 3 — 拆分 memory_module.py sub-phase complete (Tasks 7.1-7.6). Phase 3 fully complete.

Task 8.1: complete (commit 0d24a3f, batch review with 8.2-8.6 Approved; _summarize_tool_result handles dispatch_to_agent type-based errors via data.get("type") == "error", manage_user_memory success/error shapes; tool_call_id added to tool_call event)
Task 8.2: complete (commit fc943c2, batch review Approved; api.md documents tool_result + duplicate_candidate SSE events)
Task 8.3: complete (commit 825389d, batch review Approved; _render_tool_call_card/_update_tool_call_card with ui.expansion.set_text)
Task 8.4: complete (commit 825389d, see 8.3)
Task 8.5: complete (commit 825389d, see 8.3; tool_cards dict correlates tool_call_id)
Task 8.6: complete (commit 0d24a3f + 825389d, batch review Approved; 18 new unit tests, 548/548 pass, main_agent 77% coverage)

## Phase 4 — Agent 工具调用可视化 sub-phase complete (Tasks 8.1-8.7)

Task 8.7: complete (e2e browser verification 2026-07-07; report .superpowers/sdd/briefs/task-8.7-report.md; manage_user_memory ✅ card + dispatch_to_agent ❌/⏳ cards verified via cursor-ide-browser on :8088)

Task 9.1: complete (no code commit, verification-only; ruff clean, 548 passed, coverage 62.44% >= 60% gate)
Task 9.2: complete (commit pending; updated context-memory.md, flows.md, storage.md for token count / streaming / dedup / store split)
Task 9.3: complete (commit pending; updated docs/todo/05-ci-complete.md coverage status)

## Phase 5 — 收尾验证: Tasks 9.1-9.3 complete. Rollout build phase complete except 2 blocked e2e browser tasks (3.5, 4.6).
