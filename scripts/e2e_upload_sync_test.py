"""E2E API test: upload resume → parse → generate questions → verify sync endpoints."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
PDF = Path("resumes/王韬略.pdf")
TIMEOUT = httpx.Timeout(connect=10, read=300, write=30, pool=10)


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    sys.exit(1)


def chat(message: str) -> str:
    """POST /api/chat SSE, return full assistant reply."""
    reply = ""
    with httpx.Client(timeout=TIMEOUT) as client:
        with client.stream(
            "POST", f"{BASE}/api/chat", json={"message": message}
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                reply += chunk.get("delta", "")
    return reply


def main() -> None:
    if not PDF.exists():
        fail(f"PDF not found: {PDF}")

    print("\n=== E2E: Upload → Parse → Questions → Sync ===\n")

    with httpx.Client(timeout=TIMEOUT) as client:
        # 0. Clean existing candidate (if any)
        r = client.get(f"{BASE}/api/candidates", params={"limit": 50})
        r.raise_for_status()
        for c in r.json().get("candidates", []):
            cid = c["id"]
            dr = client.delete(f"{BASE}/api/candidates/{cid}")
            if dr.status_code in (200, 409):
                ok(f"removed old candidate {c.get('name')} ({cid[:8]}…)")
            else:
                fail(f"delete candidate failed: {dr.status_code} {dr.text}")

        # 1. Upload resume
        print("\n[1] Upload resume")
        with PDF.open("rb") as f:
            r = client.post(
                f"{BASE}/api/resume/upload",
                files={"file": (PDF.name, f, "application/pdf")},
            )
        r.raise_for_status()
        upload = r.json()
        candidate_id = upload["candidate_id"]
        file_path = upload["file_path"]
        safe_stem = upload["safe_stem"]
        ok(f"uploaded candidate_id={candidate_id[:8]}… file={file_path}")

        # 2. Candidates list before parse (may be empty — expected)
        print("\n[2] Candidates before parse")
        r = client.get(f"{BASE}/api/candidates", params={"limit": 50})
        r.raise_for_status()
        before = r.json()
        print(f"  count={before['total']} (empty before parse is OK)")

        # 3. Profile before parse → 404 expected
        print("\n[3] Profile before parse")
        r = client.get(
            f"{BASE}/api/resume/profile", params={"candidate_id": candidate_id}
        )
        if r.status_code == 404:
            ok("profile 404 before parse (expected)")
        else:
            fail(f"expected 404, got {r.status_code}")

        # 4. Parse resume via chat
        print("\n[4] Parse resume (LLM, ~60s)…")
        t0 = time.perf_counter()
        md_path = f"resumes/{safe_stem}.md"
        parse_msg = (
            f"简历 {file_path} 已就绪，请解析为 Markdown 并保存为 {md_path}，"
            f"解析完成后提取候选人基本信息（姓名、邮箱、电话、技能、工作年限、职位等）"
        )
        reply = chat(parse_msg)
        elapsed = time.perf_counter() - t0
        ok(f"parse chat done in {elapsed:.0f}s, reply_len={len(reply)}")

        # 5. Candidates after parse
        print("\n[5] Candidates after parse")
        r = client.get(f"{BASE}/api/candidates", params={"limit": 50})
        r.raise_for_status()
        after = r.json()
        if after["total"] < 1:
            fail(f"candidates still empty after parse: {after}")
        name = after["candidates"][0].get("name", "")
        ok(f"found candidate name={name!r} total={after['total']}")

        # 6. Profile after parse
        print("\n[6] Profile after parse")
        r = client.get(
            f"{BASE}/api/resume/profile", params={"candidate_id": candidate_id}
        )
        r.raise_for_status()
        profile_data = r.json()
        resume_md = profile_data.get("resume_markdown", "")
        profile = profile_data.get("profile", {})
        if not resume_md:
            fail("resume_markdown empty after parse")
        ok(f"resume_markdown len={len(resume_md)}, name={profile.get('name')!r}")

        # 7. Generate questions via chat
        print("\n[7] Generate questions (LLM, ~60s)…")
        t0 = time.perf_counter()
        q_msg = f"请为候选人 {profile.get('name', safe_stem)} 生成 8 道面试题目"
        q_reply = chat(q_msg)
        elapsed = time.perf_counter() - t0
        ok(f"questions chat done in {elapsed:.0f}s, reply_len={len(q_reply)}")

        # 8. Profile with questions
        print("\n[8] Profile with questions")
        r = client.get(
            f"{BASE}/api/resume/profile", params={"candidate_id": candidate_id}
        )
        r.raise_for_status()
        final = r.json()
        questions = final.get("questions", [])
        if not questions:
            fail(
                "questions still empty — MainAgent may not have dispatched to ResumeAgent. "
                f"Chat preview: {q_reply[:200]}…"
            )
        ok(f"questions count={len(questions)}")
        ok(
            f"first question: [{questions[0].get('dimension')}] {questions[0].get('question', '')[:60]}…"
        )

        # 9. Session state
        print("\n[9] Session state")
        r = client.get(f"{BASE}/api/session/current")
        r.raise_for_status()
        sess = r.json().get("session") or {}
        ok(
            f"session candidate_name={sess.get('candidate_name')!r} stage={sess.get('stage')}"
        )

    print("\n=== ALL E2E CHECKS PASSED ===\n")


if __name__ == "__main__":
    main()
