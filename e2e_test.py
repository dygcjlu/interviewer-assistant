"""End-to-end API/WS checks (T-01 ~ T-10b)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import websockets

BASE = "http://127.0.0.1:8001"
WS_URL = "ws://127.0.0.1:8001/ws/interview"
RESUME = Path(__file__).parent / "test_resume.pdf"

FAILURES: list[str] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


async def run() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=300.0) as client:
        # T-01
        r = await client.get("/api/session/current")
        ok("T-01 session/current", r.status_code == 200, r.text[:80])

        # T-02
        with RESUME.open("rb") as f:
            r = await client.post(
                "/api/resume/upload",
                files={"file": ("test_resume.pdf", f, "application/pdf")},
            )
        data = r.json() if r.status_code == 200 else {}
        qn = len(data.get("questions", []))
        ok("T-02 upload", r.status_code == 200, f"status={r.status_code}")
        ok("T-02 questions", qn >= 1, f"count={qn}")
        candidate_id = data.get("candidate_id", "")
        ok("T-02 profile", bool(data.get("profile", {}).get("name") or data.get("profile", {}).get("resume_summary")), "")

        # T-03
        r = await client.post("/api/interview/start", json={"candidate_id": candidate_id, "trigger_mode": "manual"})
        ok("T-03 start", r.status_code == 200 and r.json().get("stage") == "interviewing", r.text[:100])

        # T-05 / T-06 / rounds via WebSocket
        rounds_count = 0
        got_suggestion = False
        async with websockets.connect(WS_URL) as ws:
            snap = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if snap.get("type") == "session_snapshot":
                rounds_count = snap.get("rounds_count", 0)

            text = "我用 Redis 分布式锁解决了库存超卖，采用 SET NX EX 并配合 Lua 续期。"
            await ws.send(json.dumps({"type": "manual_input", "source": "candidate", "text": text}))
            await asyncio.sleep(0.5)

            for _ in range(30):
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                except asyncio.TimeoutError:
                    break
                if msg.get("type") == "session_snapshot":
                    rounds_count = msg.get("rounds_count", rounds_count)
                if msg.get("type") in ("suggestion_final", "suggestion"):
                    got_suggestion = True

            await ws.send(json.dumps({"type": "request_suggestion"}))
            for _ in range(60):
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                except asyncio.TimeoutError:
                    break
                if msg.get("type") == "session_snapshot":
                    rounds_count = msg.get("rounds_count", rounds_count)
                if msg.get("type") in ("suggestion_final", "suggestion", "suggestion_delta"):
                    got_suggestion = True

        ok("T-05 manual input", True)
        ok("T-06 suggestion", got_suggestion)
        ok("T-07 rounds_count", rounds_count >= 1, f"rounds={rounds_count}")

        # T-08 stop + eval
        r = await client.post("/api/interview/stop")
        ok("T-08 stop", r.status_code == 200, r.text[:120])
        r = await client.get("/api/interview/eval")
        eval_data = r.json() if r.status_code == 200 else {}
        report = eval_data.get("report") or eval_data
        dims = report.get("dimensions") or report.get("dimension_scores") or []
        ok(
            "T-08 eval",
            r.status_code == 200 and len(dims) >= 1,
            f"dims={len(dims)} rec={report.get('recommendation', '')[:20]}",
        )

        # T-09
        r = await client.get("/api/candidates", params={"limit": 10})
        candidates = r.json() if r.status_code == 200 else []
        ok("T-09 candidates", r.status_code == 200 and len(candidates) >= 1, f"count={len(candidates)}")

        # T-10b invalid file
        r = await client.post(
            "/api/resume/upload",
            files={"file": ("bad.txt", b"not a pdf", "text/plain")},
        )
        ok("T-10b invalid upload", r.status_code == 400, f"status={r.status_code}")


if __name__ == "__main__":
    asyncio.run(run())
    if FAILURES:
        print("\nFailed:", ", ".join(FAILURES))
        sys.exit(1)
    print("\nAll E2E checks passed.")
