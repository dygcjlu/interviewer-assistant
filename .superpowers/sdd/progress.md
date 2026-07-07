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
