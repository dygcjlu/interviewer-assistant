# Task 6.5 Code Review

**Commit**: `5edda17` — `test: raise main_agent/routes coverage to 70%+`  
**Reviewer**: code-reviewer agent  
**Date**: 2026-07-07  
**Assessment**: ✅ **Approved**

---

## Scope Verification

`git show 5edda17 --stat` confirms **only one file changed**: `tests/unit/test_main_agent.py` (+118 lines, -1 line). No `src/` files were modified. ✅

---

## Independent Coverage Re-Verification

Command run from worktree root:
```
python -m pytest tests/unit tests/integration \
  --cov=src.agents.main_agent --cov=src.web.routes \
  --cov-report=term-missing -q
```

Results:

| File | Stmts | Miss | Branch | BrPart | Cover |
|---|---|---|---|---|---|
| `src/agents/main_agent.py` | 284 | 71 | 102 | 13 | **74%** ✅ |
| `src/web/routes.py` | 569 | 139 | 186 | 45 | **71%** ✅ |
| TOTAL | 853 | 210 | 288 | 58 | 72% |

Both files exceed the 70% threshold. Percentages **exactly match** the implementer's claimed figures.

`routes.py` was genuinely already above 70% before this commit — the diff only touches `test_main_agent.py` and the routes coverage is identical to the pre-commit baseline reported in the task report (71%). ✅

---

## Full Suite Regression Check

```
530 passed, 1 warning in 36.35s
```

Zero regressions. The 1 warning is pre-existing (coroutine `_auto_check_coverage` never awaited in `test_context.py` — unrelated to this commit). ✅

---

## Lint

```
ruff check tests/unit/test_main_agent.py
All checks passed!
```

✅

---

## Test Quality Analysis

### `TestExtractUserFacingError` (3 tests)

All three tests verify guard-path behaviors of `_extract_user_facing_error` (lines 45–50):

| Test | Path exercised | Assertion quality |
|---|---|---|
| `test_returns_none_for_invalid_json` | `except Exception: return None` (line 48) | `assert result is None` — ✅ meaningful |
| `test_returns_none_when_user_facing_false` | `not data.get("user_facing")` (line 49) | `assert result is None` — ✅ meaningful |
| `test_returns_none_for_non_dict_json` | `not isinstance(data, dict)` (line 49) | `assert result is None` — ✅ meaningful |

Each test would catch a real regression: removing or inverting any of the guard conditions would cause one of these tests to fail (the function would either raise or return a string instead of `None`). They are not "call and assert no exception" padding.

**Observation (no action needed)**: None of the three new tests verify the *happy path* — that `_extract_user_facing_error` correctly returns the error message when `user_facing=True`. However, the coverage report confirms line 51 (the return statement) is covered, meaning this path is exercised by existing higher-level tests in `TestMainAgentHandleChat`. The implementer correctly avoided duplicating coverage that already exists.

### `TestExtractDuplicateCandidateEvent` (3 tests)

Mirrors the structure of the error extractor tests, covering the three guard paths of `_extract_duplicate_candidate_event` (lines 60–66):

| Test | Path exercised | Assertion quality |
|---|---|---|
| `test_returns_none_for_invalid_json` | `except Exception: return None` (line 61) | ✅ meaningful |
| `test_returns_none_for_non_dict_json` | `not isinstance(data, dict)` (line 63) | ✅ meaningful |
| `test_returns_none_when_dup_value_is_not_dict` | `not isinstance(dup, dict)` (line 66) | ✅ meaningful |

Same note as above: the happy path (line 67) is already covered by existing `TestMainAgentDuplicateCandidateEvent` tests and correctly not duplicated here. ✅

### `TestMainAgentSimpleMethods` (4 tests)

These are the strongest tests in the batch — they test `MainAgent` instance methods directly using `_minimal_agent()`:

| Test | Assertions | Regression value |
|---|---|---|
| `test_reload_user_memory_refreshes_layer2_and_clears_cache` | Checks `_layer2_user_memory` equals mock render value AND `_cached_system_prompt is None` | **High** — catches if either the render call is skipped, the result is not stored, or cache invalidation is removed |
| `test_clear_candidate_context_blanks_layer3_and_invalidates_cache` | Checks `_layer3_candidate == ""` AND `_cached_system_prompt is None` | **High** — catches if reset or cache-invalidation logic is removed |
| `test_set_candidate_context_includes_all_optional_fields` | Six `in ctx` string assertions + cache invalidation check | **High** — exercises all 5 optional branches in `set_candidate_context`; removing any optional field branch would fail at least one assertion |
| `test_build_system_prompt_includes_user_memory_section_when_non_empty` | Asserts section header and content appear in rendered prompt | **High** — catches if the layer-2 conditional block is removed or the section heading is changed |

All four tests follow the AAA pattern and have assertions that would catch realistic regressions.

---

## Style Consistency

The new test classes adhere to the same conventions as the pre-existing classes in the file:
- `@pytest.mark.unit` decorator ✅
- `_minimal_agent()` helper for method-level tests ✅
- Descriptive test names (`test_<what>_<when>` style) ✅
- Docstring on each test explaining purpose ✅
- Section comment header (`# ── Task 6.5 — …`) consistent with existing section headers ✅

---

## Findings

| Severity | Count |
|---|---|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

No issues found.

---

## Summary

This is a clean, purposeful test addition. The 10 new tests:
- Cover the correct uncovered lines identified by `--cov-report=term-missing`
- Have meaningful assertions that would catch real regressions (not coverage-padding)
- Do not duplicate existing coverage
- Pass all 530 tests with no regressions
- Pass ruff lint with no issues
- Are stylistically consistent with the rest of the file

**Assessment: Approved** — ready to merge.
