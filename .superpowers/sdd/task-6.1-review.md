# Task 6.1 Code Review — `tests/unit/test_main_agent.py`

**Commit**: `6c721ab` — `test: cover MainAgent tool-call loop paths`  
**Reviewer**: code-reviewer subagent  
**Date**: 2026-07-07

---

## Assessment: Approved

No Critical or Important issues found. The implementation is correct, faithful to real source behavior, and consistent with existing project test conventions.

---

## Scope Check

The commit adds exactly one file: `tests/unit/test_main_agent.py` (277 lines). No `src/` files were modified. This matches the task's "pure test addition" expectation.

---

## Findings

### Critical
_None._

### Important
_None._

### Minor

**M1 — `ConversationLogger` not mocked (acknowledged, pattern-consistent)**  
File: `tests/unit/test_main_agent.py` — `_make_agent()` helper  
`MainAgent.__init__` instantiates a `ConversationLogger` backed by `asyncio.to_thread` writes to `conversations/main_agent.jsonl`. The tests don't mock it, so the file is created on disk during test runs.  
This is explicitly acknowledged in the report and matches the pre-existing `TestMainAgentDuplicateCandidateEvent` pattern in `test_agents.py`, which makes the same trade-off. Not blocking.

**M2 — `_` throwaway variable reused in same scope**  
File: `test_main_agent.py:169`  
```python
agent, llm, _ = _make_agent(...)       # _ = tools mock
[_ async for _ in agent.handle_chat("test")]  # _ = chunk
```
Both uses of `_` are throwaway "I don't need this" idiom, so there's no functional problem, but it's worth noting as a style nit. ruff is satisfied.

**M3 — `_make_agent` is a module-level function vs. class method in prior art**  
`TestMainAgentDuplicateCandidateEvent` in `test_agents.py` uses `self._make_agent()` as an instance method. The new file uses a standalone `_make_agent()`. Both are valid; the module-level placement is arguably cleaner since it's shared across tests. Purely stylistic, no impact.

---

## Independent Verification

### Test correctness trace — key points verified against real `_handle_chat_locked`

**`tool_call` dict yield order (Path 3 — user_facing error):**
```python
# src/agents/main_agent.py (tool loop body)
yield {                                    # ← yields dict BEFORE dispatch
    "type": "tool_call",
    "name": tc.function.name,
    "args": tc.function.arguments,
}
result_str = await self._tools.dispatch(  # ← dispatch runs AFTER yield
    tc.function.name, tc.function.arguments
)
```
Confirmed: `{"type": "tool_call"}` is emitted before `dispatch()` resolves in ALL paths including the `user_facing` error short-circuit. Tests correctly assert `len(tool_events) == 1` (not `== 0`) for Path 3.

**`_extract_user_facing_error` — both fixture variants verified:**
```python
return str(data.get("message") or data.get("error") or "")
```
- Test 5 uses `{"error": "...", "user_facing": True}` → maps to `data.get("error")` ✓  
- Test 6 uses `{"type": "error", "message": "...", "user_facing": True}` → maps to `data.get("message")` ✓

**Path 2 final text yield mechanism:**  
After `llm.chat()` returns `ChatResponse(content="已完成简历解析。", tool_calls=None)`:
1. `current_tool_calls = None` and `next_resp.content` is truthy → `next_msg` appended to `_history`
2. Post-loop final stream block: `last = self._history[-1]` (the `next_msg`)
3. `need_final_stream = not (last and last.role == "assistant" and last.content)` → `False`
4. Falls to `else: yield last.content or ""` → yields `"已完成简历解析。"` as a single str chunk

Tests correctly assert `"已完成简历解析" in "".join(str_chunks)`. ✓

**`StreamChunk` / `ChatResponse` mock fidelity:**  
Both are real dataclasses from `src/llm/protocol.py`, not `MagicMock` shims. The test constructs them with correct field names (`delta`, `is_final`, `accumulated_content`, `tool_calls` for `StreamChunk`; `content`, `tool_calls` for `ChatResponse`). A real bug in streaming/tool-call logic would be caught because the mock delegates control flow to the actual `_handle_chat_locked` implementation.

**`ToolCallInfo` / `FunctionCallInfo` construction:**
```python
# Real dataclass (src/models/message.py)
@dataclass class FunctionCallInfo: name: str; arguments: str
@dataclass class ToolCallInfo: id: str; function: FunctionCallInfo; type: str = "function"

# Test helper _make_tc
return ToolCallInfo(id="tc-001", type="function", function=FunctionCallInfo(name=name, arguments=arguments))
```
Correct field names, keyword argument form works with the dataclass. ✓

**`duplicate_candidate` coverage claim:**  
`TestMainAgentDuplicateCandidateEvent` in `test_agents.py` contains two tests (`test_yields_duplicate_candidate_event_and_stops_early` and `test_duplicate_candidate_event_followed_by_human_readable_text`) at the same `handle_chat` abstraction level, covering event payload shape, early-stop (no `llm.chat` called), and human-readable text emission. The claim of adequate existing coverage is verified. ✓

---

## Independent Re-verification Output

### `pytest tests/unit/test_main_agent.py -v`
```
============================= test session starts =============================
platform win32 -- Python 3.12.13, pytest-9.0.3
asyncio: mode=Mode.AUTO

tests/unit/test_main_agent.py::TestMainAgentToolLoop::test_pure_text_yields_only_str_chunks PASSED
tests/unit/test_main_agent.py::TestMainAgentToolLoop::test_pure_text_multiple_deltas_are_yielded_incrementally PASSED
tests/unit/test_main_agent.py::TestMainAgentToolLoop::test_single_tool_call_yields_event_then_text PASSED
tests/unit/test_main_agent.py::TestMainAgentToolLoop::test_single_tool_call_dispatches_with_correct_name_and_args PASSED
tests/unit/test_main_agent.py::TestMainAgentToolLoop::test_user_facing_error_yields_error_text_and_stops PASSED
tests/unit/test_main_agent.py::TestMainAgentToolLoop::test_user_facing_error_via_message_key PASSED
tests/unit/test_main_agent.py::TestMainAgentToolLoop::test_non_user_facing_error_does_not_short_circuit PASSED

7 passed in 1.71s
```

### `pytest tests/unit tests/integration -q`
```
512 passed, 1 warning in 16.10s
```
No regressions. Count matches implementer's report.

### `ruff check tests/unit/test_main_agent.py`
```
All checks passed!
```

---

## Summary

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 0     | pass   |
| Important | 0    | pass   |
| Minor    | 3     | note   |

All 7 tests pass. Full suite 512 passed, no regressions. Ruff clean. The implementation correctly traces all three paths in `_handle_chat_locked`, uses real protocol dataclasses for mock fidelity, and handles the key nuance that `{"type": "tool_call"}` is always emitted before `dispatch()` resolves. Ready to merge.
