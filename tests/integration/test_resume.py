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
async def test_upload_duplicate_filename_no_longer_returns_409(client):
    """去重迁移到解析后按真实姓名判定（Task 4.1/4.2）：上传时仅按 PDF 文件名
    识别候选人，即使已存在同名候选人档案，上传本身也不再检测/阻断，返回 200。
    （真正的判重发生在 dispatch_to_agent 的 parse_done 分支，见 test_dispatch.py。）"""
    memory = client._transport.app.state.memory_module
    candidate = CandidateProfile(id="cid-dup-001", name="李四")
    await memory.save_candidate(candidate, "# 李四简历\n")

    r = await _upload(client, filename="李四.pdf")
    assert r.status_code == 200
    data = r.json()
    assert "candidate_id" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_same_filename_twice_succeeds(client):
    """上传接口不再做去重检查：同一文件名重复上传两次均应成功（覆盖同一 PDF 文件）。"""
    r1 = await _upload(client, filename="王五.pdf")
    assert r1.status_code == 200
    r2 = await client.post(
        "/api/resume/upload",
        files={"file": ("王五.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert r2.status_code == 200


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


# ── POST /api/resume/resolve-duplicate（Task 4.4）─────────────────────────────


def _register_pending(app, *, existing_id: str, new_id: str, name: str) -> str:
    """在 tool_ctx.pending_duplicates 里手工插入一条待决议记录，模拟判重命中后的状态。"""
    from src.tools._context import PendingResumeDuplicate
    from src.tools._context import ctx as tool_ctx

    pending_id = f"pending-{new_id}"
    tool_ctx.pending_duplicates[pending_id] = PendingResumeDuplicate(
        pending_id=pending_id,
        session_id="s-irrelevant",
        new_profile=CandidateProfile(
            id=new_id, name=name, current_position="后端工程师"
        ),
        resume_markdown=f"# {name} 新简历\n",
        existing_candidate_id=existing_id,
        existing_candidate_name=name,
    )
    return pending_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_duplicate_overwrite_sets_existing_id(client):
    """overwrite：应把 profile.id 改写为 existing_candidate_id 后保存，
    即覆盖旧档案，而不是新建一条记录。"""
    memory = client._transport.app.state.memory_module
    existing = CandidateProfile(id="cid-existing-001", name="老王")
    await memory.save_candidate(existing, "# 老王旧简历\n")

    pending_id = _register_pending(
        client._transport.app,
        existing_id="cid-existing-001",
        new_id="cid-new-001",
        name="老王",
    )

    r = await client.post(
        "/api/resume/resolve-duplicate",
        json={"pending_id": pending_id, "action": "overwrite"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["action"] == "overwrite"
    assert data["candidate_id"] == "cid-existing-001"
    assert data["candidate_name"] == "老王"

    saved = await memory.get_candidate("cid-existing-001")
    assert saved is not None
    assert saved.current_position == "后端工程师"

    from src.tools._context import ctx as tool_ctx

    assert pending_id not in tool_ctx.pending_duplicates


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_duplicate_keep_both_creates_new_record(client):
    """keep_both：existing 与 new_profile 均保留为独立档案。"""
    memory = client._transport.app.state.memory_module
    existing = CandidateProfile(id="cid-existing-002", name="老李")
    await memory.save_candidate(existing, "# 老李旧简历\n")

    pending_id = _register_pending(
        client._transport.app,
        existing_id="cid-existing-002",
        new_id="cid-new-002",
        name="老李",
    )

    r = await client.post(
        "/api/resume/resolve-duplicate",
        json={"pending_id": pending_id, "action": "keep_both"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["action"] == "keep_both"
    assert data["candidate_id"] == "cid-new-002"

    assert await memory.get_candidate("cid-existing-002") is not None
    new_saved = await memory.get_candidate("cid-new-002")
    assert new_saved is not None
    assert new_saved.current_position == "后端工程师"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_duplicate_cancel_does_not_save(client):
    """cancel：不落盘任何新档案，仅清理 pending 记录。"""
    memory = client._transport.app.state.memory_module
    pending_id = _register_pending(
        client._transport.app,
        existing_id="cid-existing-003",
        new_id="cid-new-003",
        name="老张",
    )

    r = await client.post(
        "/api/resume/resolve-duplicate",
        json={"pending_id": pending_id, "action": "cancel"},
    )
    assert r.status_code == 200
    assert r.json() == {"action": "cancel", "pending_id": pending_id}

    assert await memory.get_candidate("cid-new-003") is None

    from src.tools._context import ctx as tool_ctx

    assert pending_id not in tool_ctx.pending_duplicates


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_duplicate_overwrite_save_failure_preserves_pending(
    client, monkeypatch
):
    """save_candidate 失败时应返回 500，且 pending 记录必须原样保留：
    new_profile.id 不能被污染为 existing_candidate_id，否则用户用相同
    pending_id 重试（例如改选 keep_both）会静默保存成 existing 的 ID。"""
    memory = client._transport.app.state.memory_module
    existing = CandidateProfile(id="cid-existing-004", name="老赵")
    await memory.save_candidate(existing, "# 老赵旧简历\n")

    pending_id = _register_pending(
        client._transport.app,
        existing_id="cid-existing-004",
        new_id="cid-new-004",
        name="老赵",
    )

    from src.tools._context import ctx as tool_ctx

    original_new_profile_id = tool_ctx.pending_duplicates[pending_id].new_profile.id

    async def _boom(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(memory, "save_candidate", _boom)

    r = await client.post(
        "/api/resume/resolve-duplicate",
        json={"pending_id": pending_id, "action": "overwrite"},
    )
    assert r.status_code == 500

    assert pending_id in tool_ctx.pending_duplicates
    pending = tool_ctx.pending_duplicates[pending_id]
    assert pending.new_profile.id == original_new_profile_id
    assert pending.new_profile.id == "cid-new-004"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_duplicate_unknown_pending_id_returns_404(client):
    r = await client.post(
        "/api/resume/resolve-duplicate",
        json={"pending_id": "does-not-exist", "action": "cancel"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "pending_not_found"
