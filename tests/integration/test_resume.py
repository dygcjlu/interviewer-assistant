"""Integration tests — POST /api/resume/upload 和 GET /api/resume/profile"""

from __future__ import annotations

import pytest

from src.models.candidate import CandidateProfile

# ── helpers ───────────────────────────────────────────────────────────────────


def _pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<</Type /Catalog /Pages 2 0 R>>\nendobj\n"
        b"2 0 obj\n<</Type /Pages /Kids [3 0 R] /Count 1>>\nendobj\n"
        b"3 0 obj\n<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]>>\nendobj\n"
        b"xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n"
        b"0000000058 00000 n\n0000000115 00000 n\n"
        b"trailer\n<</Size 4 /Root 1 0 R>>\nstartxref\n190\n%%EOF\n"
    )


async def _upload(client, filename="测试候选人.pdf", content: bytes | None = None):
    data = content if content is not None else _pdf_bytes()
    return await client.post(
        "/api/resume/upload",
        files={"file": (filename, data, "application/pdf")},
    )


# ── upload 正常路径 ────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_valid_pdf_returns_200(client):
    r = await _upload(client)
    assert r.status_code == 200
    data = r.json()
    assert "file_path" in data
    assert "safe_stem" in data
    assert "candidate_id" in data
    assert "session_id" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_returns_safe_stem_from_filename(client):
    r = await _upload(client, filename="张三_后端.pdf")
    assert r.status_code == 200
    assert r.json()["safe_stem"] == "张三_后端"


# ── upload 错误路径 ────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_non_pdf_returns_400(client):
    r = await client.post(
        "/api/resume/upload",
        files={"file": ("resume.txt", b"not a pdf", "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_file_type"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_duplicate_name_returns_409(client):
    """已存在同名候选人时（已在 memory 中），上传同名 PDF 返回 409 duplicate_candidate。"""
    # 先通过 memory 直接保存候选人（模拟已完成解析的候选人）
    memory = client._transport.app.state.memory_module
    candidate = CandidateProfile(id="cid-dup-001", name="李四")
    await memory.save_candidate(candidate, "# 李四简历\n")

    # 上传同名 PDF（不带 overwrite）
    r = await _upload(client, filename="李四.pdf")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "duplicate_candidate"
    assert "existing_candidate_id" in detail


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_overwrite_replaces_existing(client):
    """overwrite=true 时不报 409。"""
    await _upload(client, filename="王五.pdf")
    r = await client.post(
        "/api/resume/upload",
        files={"file": ("王五.pdf", _pdf_bytes(), "application/pdf")},
        params={"overwrite": "true"},
    )
    assert r.status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_too_large_returns_413(client):
    """超过 20MB 上限返回 413 file_too_large。"""
    big_content = b"%PDF-1.4\n" + b"X" * (21 * 1024 * 1024)
    r = await client.post(
        "/api/resume/upload",
        files={"file": ("large.pdf", big_content, "application/pdf")},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "file_too_large"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_during_interview_returns_409(interviewing_client):
    """面试进行中上传新简历返回 409 interview_in_progress。"""
    r = await _upload(interviewing_client, filename="新候选人.pdf")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "interview_in_progress"


# ── GET /api/resume/profile ───────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_profile_returns_candidate_data(client):
    """先保存候选人，再通过接口获取 profile。"""
    memory = client._transport.app.state.memory_module
    candidate = CandidateProfile(id="cid-profile-001", name="赵六")
    await memory.save_candidate(candidate, "# 赵六的简历\n")

    r = await client.get(
        "/api/resume/profile", params={"candidate_id": "cid-profile-001"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["candidate_id"] == "cid-profile-001"
    assert data["profile"]["name"] == "赵六"
    assert "brief" in data
    assert "resume_markdown" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_profile_nonexistent_returns_404(client):
    r = await client.get("/api/resume/profile", params={"candidate_id": "nonexistent"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"
