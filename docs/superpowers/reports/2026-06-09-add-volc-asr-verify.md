# Verification Report: add-volc-asr

**Date:** 2026-06-09  
**Verifier:** comet-verify (full mode)  
**Base ref:** `6824645f`  
**Committed files:** 7 (`src/audio/volc_stt.py`, `src/audio/protocol.py`, `src/config.py`, `src/main.py`, `tests/unit/test_volc_stt.py`, design doc, plan)

---

## Summary

| Dimension    | Status                              |
|--------------|-------------------------------------|
| Completeness | 20/22 tasks ✓ (2 manual E2E pending)|
| Correctness  | 28/28 unit tests pass; all design decisions implemented |
| Coherence    | Follows STTEngine protocol; consistent with baidu/xunfei patterns |

---

## Issues

### WARNING

- **tasks 5.2 & 5.3 not checked** (`openspec/changes/add-volc-asr/tasks.md` lines 35–36): These are manual real-world smoke tests requiring live Volc credentials and audio hardware. Implementation is complete; these validations must be performed by the user in their deployment environment before trusting the engine in production.

---

## Verification Detail

### Completeness

- Tasks complete: **20/22** `[x]`
- Remaining unchecked:
  - `5.2` Start service with real `VOLC_APP_KEY/VOLC_ACCESS_KEY`, begin an interview with `STT_ENGINE=volc`, confirm live captions appear
  - `5.3` Confirm follow-up suggestions trigger on candidate speech pause (`is_final=True` / `definite=true` path)
- No delta specs directory found (`openspec/changes/add-volc-asr/specs/` — no capability spec files)

### Correctness

All 28 unit tests pass:
```
tests/unit/test_volc_stt.py  28 passed in 0.48s
```

Design decision verification:

| Decision | Expected | Actual in `volc_stt.py` |
|----------|----------|--------------------------|
| D1: Endpoint | `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel` | ✓ `_WSS_URL` line 24 |
| D2: No compression | `compression=0x00` in headers | ✓ `0x10` byte2 (serialization=JSON, compression=none) |
| D3: show_utterances + single | `show_utterances=True`, `result_type="single"`, `definite` → `is_final` | ✓ lines 171–172, 244 |
| D4: 6400-byte chunk | `_SEND_CHUNK_BYTES = 6400` | ✓ line 28 |
| D5: Old-console auth headers | `X-Api-App-Key`, `X-Api-Access-Key`, `X-Api-Resource-Id` | ✓ lines 150–153 |

### Coherence

- `VolcRealtimeSTT` fully implements `STTEngine` Protocol: `connect / send_audio / receive / close` ✓
- Credential-missing behavior matches `BaiduRealtimeSTT`: silent return + WARNING log ✓
- Auto-reconnect on disconnect matches existing pattern ✓
- File naming: `volc_stt.py` consistent with `baidu_stt.py` / `xunfei_stt.py` ✓
- `STT_ENGINE=volc` factory branch in `main.py` consistent with `xunfei`/`baidu` branches ✓
- `.env` placeholder comments added ✓
- `VOLC_RESOURCE_ID` defaults to `volc.bigasr.sauc.duration` (matches D2/Open Questions design intent) ✓
- No hardcoded secrets; no new unsafe operations ✓

---

## Final Assessment

No CRITICAL issues. 1 WARNING (manual E2E tests 5.2/5.3 require live credentials — not automatable). **Ready for archive.**
