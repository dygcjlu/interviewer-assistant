"""E2E tests — 完整用户旅程（真实服务器 + 真实 LLM）。

断言策略：只验证结构和约束，不验证 LLM 生成的具体内容。
每个 test_* 函数依赖独立的 httpx 客户端（session 级 live_server）。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import websockets

TIMEOUT = httpx.Timeout(120.0)  # LLM 调用可能较慢


# ── T-01 健康检查 ─────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_health_ok(live_server):
    r = httpx.get(f"{live_server}/api/health", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["controller"] is True
    assert data["memory"] is True


# ── T-02 上传简历 ─────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_upload_resume_returns_candidate_id(live_server, e2e_resume_pdf):
    with open(e2e_resume_pdf, "rb") as f:
        r = httpx.post(
            f"{live_server}/api/resume/upload",
            files={"file": ("e2e_候选人.pdf", f, "application/pdf")},
            timeout=TIMEOUT,
        )
    assert r.status_code == 200
    data = r.json()
    assert "candidate_id" in data
    assert data["candidate_id"] is not None
    assert "file_path" in data
    assert "safe_stem" in data


@pytest.mark.e2e
def test_upload_invalid_file_returns_400(live_server):
    r = httpx.post(
        f"{live_server}/api/resume/upload",
        files={"file": ("bad.txt", b"not a pdf", "text/plain")},
        timeout=10,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_file_type"


# ── T-03 候选人列表 ───────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_list_candidates_after_upload(live_server, e2e_resume_pdf):
    """上传简历后候选人列表非空。"""
    # 确保已上传（幂等：同名返回 409，忽略即可）
    with open(e2e_resume_pdf, "rb") as f:
        httpx.post(
            f"{live_server}/api/resume/upload",
            files={"file": ("e2e_候选人.pdf", f, "application/pdf")},
            timeout=TIMEOUT,
        )

    r = httpx.get(f"{live_server}/api/candidates", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert len(data["candidates"]) >= 1


# ── T-04 选择候选人 ───────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_select_candidate_returns_profile(live_server, e2e_resume_pdf):
    """上传并获取候选人列表，然后选中第一个，验证 select 响应结构。"""
    with open(e2e_resume_pdf, "rb") as f:
        httpx.post(
            f"{live_server}/api/resume/upload",
            files={"file": ("e2e_候选人.pdf", f, "application/pdf")},
            timeout=TIMEOUT,
        )

    r_list = httpx.get(f"{live_server}/api/candidates", timeout=10)
    candidates = r_list.json()["candidates"]
    assert len(candidates) >= 1

    cid = candidates[0]["id"]
    r = httpx.post(
        f"{live_server}/api/candidate/select",
        json={"candidate_id": cid},
        timeout=30,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["candidate_id"] == cid
    assert "profile" in data
    assert "brief" in data
    assert "resume_markdown" in data


# ── T-05 面试生命周期 ──────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_interview_lifecycle(live_server, e2e_resume_pdf):
    """start → WebSocket 收 session_snapshot → stop → eval 完整流程。"""
    # 准备候选人
    with open(e2e_resume_pdf, "rb") as f:
        up = httpx.post(
            f"{live_server}/api/resume/upload",
            files={"file": ("e2e_候选人.pdf", f, "application/pdf")},
            params={"overwrite": "true"},
            timeout=TIMEOUT,
        )
    cid = up.json().get("candidate_id") or _get_first_candidate_id(live_server)

    # 开始面试
    r_start = httpx.post(
        f"{live_server}/api/interview/start",
        json={"candidate_id": cid, "trigger_mode": "manual"},
        timeout=30,
    )
    assert r_start.status_code == 200
    assert r_start.json()["stage"] == "interviewing"

    # WebSocket：收 session_snapshot
    asyncio.run(_assert_ws_snapshot(live_server))

    # 停止面试
    r_stop = httpx.post(f"{live_server}/api/interview/stop", timeout=30)
    assert r_stop.status_code == 200
    assert r_stop.json()["stage"] == "evaluating"

    # 生成评价报告（真实 LLM，验证结构）
    r_eval = httpx.get(f"{live_server}/api/interview/eval", timeout=TIMEOUT)
    assert r_eval.status_code == 200
    report = r_eval.json()["report"]
    _assert_eval_report_structure(report)


def _get_first_candidate_id(base: str) -> str:
    r = httpx.get(f"{base}/api/candidates", timeout=10)
    return r.json()["candidates"][0]["id"]


async def _assert_ws_snapshot(base: str) -> None:
    ws_url = base.replace("http", "ws") + "/ws/interview"
    async with websockets.connect(ws_url) as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        msg = json.loads(raw)
        assert msg["type"] == "session_snapshot"
        assert "stage" in msg
        assert "rounds_count" in msg


def _assert_eval_report_structure(report: dict) -> None:
    """验证评价报告结构约束（不检查具体内容）。"""
    assert "dimensions" in report, "报告缺少 dimensions 字段"
    assert isinstance(report["dimensions"], list), "dimensions 应为列表"
    assert len(report["dimensions"]) >= 1, "dimensions 至少有一个维度"

    for dim in report["dimensions"]:
        assert "dimension" in dim
        assert "score" in dim
        assert isinstance(dim["score"], (int, float))
        assert 1 <= dim["score"] <= 10, f"score 超出 1-10 范围: {dim['score']}"
        assert "evidence" in dim
        assert isinstance(dim["evidence"], list)

    assert "overall_score" in report
    assert 1 <= report["overall_score"] <= 10
    assert "recommendation" in report
    assert report["recommendation"] in (
        "strong_hire",
        "hire",
        "weak_hire",
        "no_hire",
    ), f"非法 recommendation 值: {report['recommendation']}"
    assert "summary" in report
    assert "strengths" in report
    assert "weaknesses" in report


# ── T-06 健康检查 + 恢复扫描（无残留） ──────────────────────────────────────


@pytest.mark.e2e
def test_recovery_scan_returns_ok(live_server):
    r = httpx.get(f"{live_server}/api/recovery/scan", timeout=10)
    assert r.status_code == 200
    assert "orphans" in r.json()
    assert "count" in r.json()
