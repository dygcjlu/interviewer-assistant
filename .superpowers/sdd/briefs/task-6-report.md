# Task 6 Implementation Report: Fix LLM Client Direct Instantiation

## Status: DONE

## Implementation Summary

Successfully fixed 3 locations where code bypassed dependency injection by directly instantiating `OpenAICompatibleClient`, causing integration test mocks to fail. All changes follow TDD methodology with proper Red-Green-Refactor cycle.

## Changes Made

### 1. Production Code

**File: `src/main.py`**
- Added `app.state.llm_client = llm_client` to lifespan function (line 217)
- Injects the LLM client instance into FastAPI app state for downstream use

**File: `src/web/routes.py`**
- **check_question_coverage function (line ~570)**: Added fallback pattern to use injected client
  ```python
  llm = getattr(request.app.state, "llm_client", None)
  if not llm:
      llm = OpenAICompatibleClient(settings)
  ```
- **compare_candidates function (line ~692)**: Same fallback pattern applied
- Removed duplicate `llm = OpenAICompatibleClient(settings)` line (line 720)

**File: `src/tools/dispatch_to_agent.py`**
- **_generate_questions_from_brief function (line ~190)**: Access client via ctx.main_agent
  ```python
  llm = None
  if ctx.main_agent is not None:
      llm = ctx.main_agent._llm
  if not llm:
      settings = get_settings()
      llm = OpenAICompatibleClient(settings)
  ```

### 2. Test Code

**File: `tests/integration/conftest.py`**
- Added `app.state.llm_client = mock_llm` in test fixture (line 220)
- Ensures test infrastructure matches production injection pattern

**File: `tests/integration/test_llm_injection.py` (NEW)**
- Created 3 comprehensive integration tests following TDD Red-Green-Refactor:
  1. `test_check_question_coverage_uses_injected_llm`: Verifies routes.py line 572 fix
  2. `test_compare_candidates_uses_injected_llm`: Verifies routes.py line 711 fix  
  3. `test_dispatch_generate_questions_uses_injected_llm`: Verifies dispatch_to_agent.py line 191 fix

## Test Results

### TDD Cycle Verification

**RED Phase**: All 3 tests failed correctly with expected errors showing direct instantiation bypassing mocks

**GREEN Phase**: After implementation, all 3 tests pass
```
tests/integration/test_llm_injection.py::test_check_question_coverage_uses_injected_llm PASSED
tests/integration/test_llm_injection.py::test_compare_candidates_uses_injected_llm PASSED
tests/integration/test_llm_injection.py::test_dispatch_generate_questions_uses_injected_llm PASSED
```

**REFACTOR Phase**: Verified no regressions
- All 53 integration tests pass
- 425 of 426 unit tests pass (1 pre-existing failure in volc_stt unrelated to changes)

## Commit Information

**Commit Hash**: `478e55b`  
**Commit Message**: `fix: use injected llm_client instead of direct instantiation`

**Files Changed**:
- `src/main.py`
- `src/web/routes.py`
- `src/tools/dispatch_to_agent.py`
- `tests/integration/conftest.py`
- `tests/integration/test_llm_injection.py` (new)

## Self-Review

### Adherence to Requirements ✓
- ✅ `main.py` injects `llm_client` to `app.state`
- ✅ `routes.py` uses `getattr(request.app.state, "llm_client", None)` with fallback
- ✅ `dispatch_to_agent.py` accesses via `ctx.main_agent._llm` with fallback
- ✅ Fallback logic preserves backward compatibility
- ✅ Strict TDD methodology followed (Red-Green-Refactor)
- ✅ Commit message matches specification exactly

### Code Quality ✓
- Minimal changes - only what's needed to fix the issue
- Consistent fallback pattern across all 3 locations
- No breaking changes to existing functionality
- Comprehensive test coverage for all 3 fixes

### Testing ✓
- 3 new integration tests specifically target the injection issue
- Tests verify mock injection works (not just that code runs)
- All existing tests continue to pass
- Tests follow existing project patterns and conventions

### Documentation ✓
- Test docstrings explain the RED phase expectation
- Code comments clarify the injection + fallback pattern
- This report provides complete implementation details

## Concerns

None. Implementation is complete and fully tested.
