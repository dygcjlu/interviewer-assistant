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

Task 3.6: complete (commit e70b209, individual review Approved; cancels genuinely-different pre-existing task before creating new one in _on_trigger_fired(), verified no conflict with Task 3.3/3.4's self-cancellation guard, inlined rather than extracted shared helper)

## Phase 2 — 追问建议真流式输出 sub-phase: Tasks 3.1-3.4, 3.6 complete; Task 3.5 (e2e browser) still pending — blocked on cursor-ide-browser MCP unavailable this session

Task 4.1: complete (design/investigation task, no code commit; report .superpowers/sdd/task-4.1-report.md; corrected brief's wrong assumption that dispatch_to_agent.py is an SSE generator, redesigned event short-circuit to live in main_agent.py tool loop; identified premature session.candidate mutation risk and designed dataclasses.replace-based fix)
Task 4.2: complete (commit 1040a5b, batch review with 4.4 Approved after 1 fix round; fix commit a2654e3 for shared pending-object mutation bug in resolve_duplicate's overwrite branch, re-review Approved)
Task 4.4: complete (commit 1040a5b + fix a2654e3, batch review with 4.2 — see above; same fix round covers both)

Task 4.3: complete (commit 06e8921, individual review Approved; corrected implementer's brief in-flight — threaded new `state` param through `_chat_stream`/`_trigger_parse` since plan's sample code assumed access that didn't exist; removed dead `_confirm_overwrite_dialog`/`_conflict` upload-time 409 path; 2 accepted Minor findings, neither blocking)

## Phase 2 — 候选人去重改为按真实姓名 sub-phase: Tasks 4.1, 4.2, 4.3, 4.4 complete (backend + frontend); Task 4.6 (e2e browser, likely blocked same as 3.5) still pending; Task 4.5 (tests) backend portion covered via TDD in 4.2/4.4, frontend portion has no dedicated automated test (ui.py near-zero coverage is a pre-existing accepted state) — real UX validation deferred to Task 4.6

Task 5.1: complete (commit 2ed6301, individual review Approved; CJK assertions genuinely executed — not skipped — on this Windows machine via SimHei font; pymupdf already a declared dependency, no new deps added)

## Phase 2 — PDF 导出中文渲染测试 sub-phase complete (Task 5.1). Phase 2 as a whole now only missing the two blocked e2e browser tasks (3.5, 4.6).

Task 6.1: complete (commit 6c721ab, individual review Approved; verified real {"type":"tool_call"} timing precedes dispatch() resolution even on error short-circuit path, adjusted assertions to match; skipped duplicating duplicate_candidate coverage already in test_agents.py)
