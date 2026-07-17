# Task 7 Implementation Report: Backend-Triggered Coverage Check

## Implementation Summary

Successfully implemented backend-triggered coverage check after suggestion generation, replacing the frontend polling mechanism. The implementation follows TDD methodology with complete test coverage.

## File Changes

### 1. `src/web/routes.py`
- **Added** `_auto_check_coverage()` function (59 lines)
  - Extracts coverage check logic for reuse
  - Accepts `memory`, `llm_client`, `candidate_id`, `session` parameters
  - Builds conversation text from `session.rounds`
  - Calls LLM to analyze coverage against uncovered questions
  - Updates question coverage via `memory.update_question_coverage()`
  - Silent exception handling with `logger.warning()` on failure
- **Preserved** `/api/check-coverage` endpoint (no deletion, as specified)

### 2. `src/tools/dispatch_to_agent.py`
- **Added** `import asyncio` at module level (line 4)
- **Removed** local `import asyncio` at line 173 (conflicted with module-level import)
- **Modified** `_apply_side_effects()` function
  - Added coverage check trigger after `result_type == "suggestion"`
  - Uses `asyncio.create_task()` for non-blocking execution
  - Checks `ctx.memory_module` and `ctx.main_agent` availability
  - Passes `ctx.main_agent._llm` as `llm_client` parameter
  - Passes `session.candidate.id` and current `session`

### 3. `tests/unit/test_auto_coverage_check.py`
- **Created** new test file (207 lines)
- **TestAutoCheckCoverage** class (4 tests)
  - `test_auto_check_coverage_updates_question_coverage`: Verifies LLM-based coverage update
  - `test_auto_check_coverage_skips_when_no_questions`: Early return when no questions exist
  - `test_auto_check_coverage_skips_when_no_rounds`: Early return when no conversation rounds
  - `test_auto_check_coverage_silently_handles_exceptions`: Exception handling doesn't propagate
- **TestDispatchSideEffectWithCoverage** class (2 tests)
  - `test_apply_side_effects_triggers_coverage_check_after_suggestion`: Verifies `asyncio.create_task()` called
  - `test_apply_side_effects_skips_coverage_check_for_other_types`: No trigger for non-suggestion types

## Test Results

### New Tests
```
tests/unit/test_auto_coverage_check.py::TestAutoCheckCoverage::test_auto_check_coverage_updates_question_coverage PASSED
tests/unit/test_auto_coverage_check.py::TestAutoCheckCoverage::test_auto_check_coverage_skips_when_no_questions PASSED
tests/unit/test_auto_coverage_check.py::TestAutoCheckCoverage::test_auto_check_coverage_skips_when_no_rounds PASSED
tests/unit/test_auto_coverage_check.py::TestAutoCheckCoverage::test_auto_check_coverage_silently_handles_exceptions PASSED
tests/unit/test_auto_coverage_check.py::TestDispatchSideEffectWithCoverage::test_apply_side_effects_triggers_coverage_check_after_suggestion PASSED
tests/unit/test_auto_coverage_check.py::TestDispatchSideEffectWithCoverage::test_apply_side_effects_skips_coverage_check_for_other_types PASSED
```
**6/6 tests passed**

### Full Unit Test Suite
```
432 tests collected
431 passed, 1 failed (pre-existing unrelated failure in test_volc_stt.py)
1 warning (expected: unawaited coroutine in mock test)
```
**No regressions introduced**

## Commit Information

- **Commit Hash**: `3c8578f6c512bd5897f70f4397b7dc84090561f9`
- **Commit Message**: `feat: trigger coverage check from backend after suggestion`
- **Commit Range**: `478e55b..3c8578f`

## TDD Compliance

### RED Phase
- Wrote 6 failing tests first
- Verified all tests failed for correct reasons:
  - `ImportError` for missing `_auto_check_coverage` function
  - `AttributeError` for missing `asyncio` import

### GREEN Phase
- Implemented minimal code to pass tests:
  1. Added `_auto_check_coverage()` function in `routes.py`
  2. Added `import asyncio` in `dispatch_to_agent.py`
  3. Modified `_apply_side_effects()` to trigger coverage check
  4. Removed conflicting local `import asyncio`
- All 6 new tests passed
- No existing tests broken

### REFACTOR Phase
- Code is already minimal and clean
- No duplication to extract
- Exception handling properly scoped
- Async task properly non-blocking

## Acceptance Criteria Verification

✅ **Coverage check triggered after suggestion generation**
- `_apply_side_effects()` calls `asyncio.create_task()` when `result_type == "suggestion"`
- Verified by `test_apply_side_effects_triggers_coverage_check_after_suggestion`

✅ **Async task failure is silent and doesn't affect interview flow**
- Exception caught in `_auto_check_coverage()` with `logger.warning()`
- Verified by `test_auto_check_coverage_silently_handles_exceptions`

✅ **Frontend coverage display still works**
- `/api/check-coverage` endpoint preserved (not deleted)
- Frontend can still call it manually or display coverage data

## Self-Review

### Strengths
1. **Pure TDD approach**: All tests written before implementation
2. **Complete test coverage**: 6 tests covering main path, edge cases, and error handling
3. **Non-blocking design**: Uses `asyncio.create_task()` for background execution
4. **Silent failure**: Exception handling prevents interview disruption
5. **No breaking changes**: Existing endpoint preserved, no API changes

### Considerations
1. **No integration test**: Unit tests only, no end-to-end coverage check verification
2. **Coroutine warning in tests**: Expected behavior when mocking `create_task`, but could confuse future maintainers
3. **Cross-module import**: `routes.py` function imported in `dispatch_to_agent.py` creates coupling

### Recommendations for Future Work
1. Add integration test simulating full suggestion → coverage check flow
2. Consider moving `_auto_check_coverage()` to a shared module (e.g., `src/coverage/`) to reduce coupling
3. Add telemetry/metrics for coverage check success/failure rates in production

## Status

**DONE**

All requirements met, tests pass, no regressions, following TDD methodology throughout.
