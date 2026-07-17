# Code Review: Task 4.3 — 前端三选一去重对话框

**Commit:** `06e8921`
**File:** `src/web/ui.py` only
**Reviewer:** code-reviewer subagent
**Date:** 2026-07-07

---

## Assessment: Approved

No Critical or Important issues. Two Minor observations documented below. Independent ruff and test re-runs confirm the implementer's claimed results.

---

## Independent Verification (Step 6)

### Ruff check

```
$ ruff check src/web/ui.py
All checks passed!
```

Exit code 0. Clean.

### Test suite

```
$ python -m pytest tests/unit tests/integration -q

collected 504 items
... (all pass) ...
======================= 504 passed, 1 warning in 15.50s =======================
```

Exit code 0. 504 passed, 1 pre-existing warning (unrelated coroutine in `test_auto_coverage_check.py`). Matches the implementer's claimed result exactly.

---

## Findings

### Minor 1 — Misleading fallback in `existing_name` extraction

**File:** `src/web/ui.py:1030-1032`

```python
existing_name = chunk.get(
    "existing_candidate_name", chunk.get("new_name", "")
)
```

The fallback uses `new_name` if `existing_candidate_name` is absent. The dialog header reads `"候选人「{existing_name}」已存在"` — if the fallback fired, it would display the *new* candidate's name in a sentence that says the *existing* candidate already exists: semantically inverted and potentially confusing.

**Impact:** None in practice. The backend (`dispatch_to_agent.py:187`) always includes `existing_candidate_name` in the event, so this fallback is unreachable in the current codebase.

**Recommendation:** Consider using an empty string as the fallback (or asserting the field is present) to make the code's intent explicit and to avoid confusion if the backend contract ever changes:

```python
existing_name = chunk.get("existing_candidate_name", "")
```

### Minor 2 — `r.raise_for_status()` is called before inspecting the action; cancel-case produces a slightly surprising exception path if the server were ever to return 4xx for cancel

**File:** `src/web/ui.py:1044`

```python
r.raise_for_status()
resolved = r.json()
```

The cancel path returns HTTP 200, so `raise_for_status()` passes and the code works correctly today. However, the HTTP error path and the cancel-success path share the same exception catch, meaning a future server-side error on cancel would show a raw `httpx.HTTPStatusError` message to the user rather than a friendlier "cancel failed" message. This is a cosmetic concern on a solo-developer tool.

**Impact:** None today. Low cosmetic debt.

---

## Review Checklist Walkthrough

### 1. SSE Branch Correctness

**Field names**: The frontend reads `pending_id`, `existing_candidate_name`, `new_name` from the SSE chunk. Cross-checked against `dispatch_to_agent.py:184-189`:

```python
result["duplicate_candidate"] = {
    "pending_id": pending_id,
    "existing_candidate_id": existing.id,
    "existing_candidate_name": existing.name,
    "new_name": real_name,
}
```

All field names match exactly. ✓

**Resolve request body**: Frontend sends `{"pending_id": chunk.get("pending_id", ""), "action": action}`. Backend `ResolveDuplicateRequest` requires `pending_id` and `action`. ✓

**Cancel path**: Backend returns HTTP 200 + `{"action": "cancel", "pending_id": ...}` (no `candidate_id`). Frontend checks `resolved.get("action") == "cancel"` → shows cancel bubble. Does not attempt to read `candidate_id`/`candidate_name` from cancel response. ✓

**Overwrite/keep_both path**: Backend returns HTTP 200 + `{"action": ..., "candidate_id": ..., "candidate_name": ..., "session_id": ...}`. Frontend's `elif resolved:` branch fires, updates `state["candidate_id"]` and `state["candidate_name"]`. ✓

**404 / 500 error path**: `r.raise_for_status()` raises `httpx.HTTPStatusError`, caught by `except Exception as exc`, `_error()` bubble shown, `resolved` stays `None`, neither success nor cancel branch runs. ✓

**Network exception path**: Caught by same `except Exception`. `_error()` bubble shown. ✓

### 2. Dead Code Removal Correctness

**`_confirm_overwrite_dialog`**: `rg "_confirm_overwrite_dialog" tests/ src/` returns no matches. Zero remaining references. Correctly removed. ✓

**`_conflict` / 409 branch in `_handle_upload`**: `routes.py:upload_resume` only raises 409 for `interview_in_progress` (lines 219-229). The 409-dedup path was removed in Task 4.2 and replaced by post-parse name comparison. The removed `_conflict` branch was genuinely unreachable. Correctly removed. ✓

**`candidate_id` / `overwrite` params in `_do_upload_request`**: These params were used exclusively in the `_conflict` retry path (passing `candidate_id=existing_id, overwrite=True`). With that path gone, the params serve no purpose. The `overwrite` query param was not even parsed by the backend (`routes.py:upload_resume` has no `overwrite` parameter). Correctly removed. ✓

### 3. Regressions

**`state["candidate_id"]` from initial upload response**: After removal of the old params, `_handle_upload` still reads `cid = data.get("candidate_id", "")` from the upload response (line 943) and sets `state["candidate_id"] = cid` (line 944). This is the *response field* from `POST /api/resume/upload`, not the removed *request param*. These are distinct; the removal does not affect this assignment. ✓

**Other `_handle_upload` callers**: Only one call site (`_do_upload_left` at line 164-175). No call passes `candidate_id`/`overwrite` — they were internal to the old `_do_upload_request` closure. ✓

**`_trigger_parse` caller in `_handle_upload`**: `state=state` is correctly threaded (line 962). ✓

### 4. UX / `on_complete` correctness

`on_complete` = `_sync_candidate_panel` is a closure over the page-level `state` dict (line 82). It reads `state.get("candidate_id")` at call time (line 313).

Sequence for successful resolve:
1. SSE loop receives `duplicate_candidate` event.
2. Dialog shown; user picks action.
3. `resolve-duplicate` POST executed; on 200, `state["candidate_id"]` and `state["candidate_name"]` updated (lines 1057-1062) — **before** the SSE loop exits.
4. Remaining SSE (agent text delta + `[DONE]`) processed.
5. Loop exits normally; `finally` block calls `await on_complete()` (line 1096-1098).
6. `_sync_candidate_panel` uses the freshly-set `state["candidate_id"]` → fetches correct profile, updates panel. ✓

Sequence for cancel:
- `state["candidate_id"]` retains the session-generated ID from the upload step.
- `_sync_candidate_panel` fetches that profile → likely gets a nearly-empty profile (no real name yet) or 404 → returns early at line 322. Panel stays in its previous state. Acceptable. ✓

### 5. Style / Consistency

- `asyncio.get_event_loop().create_future()` pattern matches the removed `_confirm_overwrite_dialog` exactly. ✓
- Shared `_choose(action)` closure with `done.done()` guard is cleaner than the old separate `_cancel`/`_confirm` closures. ✓
- Button props (`flat dense`, `outline dense`, `unelevated dense color=negative`) are consistent with existing dialog conventions. ✓
- `_error(chat_col, ...)` used for error display matches existing convention. ✓
- `logger.exception(...)` used for logging matches existing convention. ✓

---

## Review Summary

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 0     | pass   |
| Important | 0    | pass   |
| Minor    | 2     | note   |

**Verdict: APPROVED** — No Critical or Important issues. Both Minor observations are either unreachable in practice (Minor 1) or cosmetic debt on a single-developer local tool (Minor 2). Safe to merge as-is; the Minor items are call-outs for awareness only.
