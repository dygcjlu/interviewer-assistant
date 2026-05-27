"""Tests for src/storage/memory_module.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.models.candidate import CandidateProfile
from src.models.evaluation import DimensionScore, EvalReport
from src.models.session import (
    ConversationRound,
    InterviewQuestion,
    InterviewSession,
    InterviewStage,
    SessionMetadata,
)
from src.storage.memory_module import MemoryModule


@pytest.fixture
def memory(tmp_path: Path) -> MemoryModule:
    return MemoryModule(candidates_dir=str(tmp_path / "candidates"))


def _make_profile(id: str = "c-1", name: str = "张三") -> CandidateProfile:
    return CandidateProfile(
        id=id,
        name=name,
        email="zhangsan@example.com",
        phone="13800000000",
        skills=["Python", "Go"],
        years_of_experience=5,
        current_position="后端工程师",
        resume_content="raw resume",
    )


def _make_session(interview_id: str = "i-1", candidate_id: str = "c-1") -> InterviewSession:
    start = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)
    return InterviewSession(
        id=interview_id,
        candidate=_make_profile(candidate_id),
        question_plan=[
            InterviewQuestion(id=1, dimension="算法", question="排序题", follow_ups=["复杂度?"]),
        ],
        rounds=[
            ConversationRound(round_number=1, interviewer_text="q1", candidate_text="a1"),
            ConversationRound(
                round_number=2,
                interviewer_text="q2",
                candidate_text="a2",
                llm_suggestion="深入追问",
            ),
        ],
        stage=InterviewStage.COMPLETED,
        context_summary="本次面试摘要",
        covered_dimensions={"算法"},
        metadata=SessionMetadata(
            candidate_id=candidate_id,
            start_time=start,
            end_time=start + timedelta(hours=1),
            trigger_mode="auto",
            total_rounds=2,
            total_prompt_tokens=500,
            total_completion_tokens=200,
        ),
    )


# ─── 候选人 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_and_get_candidate_roundtrip(memory: MemoryModule) -> None:
    profile = _make_profile()
    cid = await memory.save_candidate(profile, profile.resume_content)
    assert cid == "c-1"

    loaded = await memory.get_candidate("c-1")
    assert loaded is not None
    assert loaded.id == "c-1"
    assert loaded.name == "张三"
    assert loaded.email == "zhangsan@example.com"
    assert loaded.phone == "13800000000"
    assert loaded.skills == ["Python", "Go"]
    assert await memory.get_resume_markdown("c-1") == "raw resume"


@pytest.mark.asyncio
async def test_save_candidate_generates_id_when_empty(memory: MemoryModule) -> None:
    profile = _make_profile(id="", name="王五")
    cid = await memory.save_candidate(profile, profile.resume_content)
    assert cid.startswith("c-")
    loaded = await memory.get_candidate(cid)
    assert loaded is not None
    assert loaded.name == "王五"


@pytest.mark.asyncio
async def test_get_candidate_missing_returns_none(memory: MemoryModule) -> None:
    assert await memory.get_candidate("missing") is None


@pytest.mark.asyncio
async def test_search_candidates_filters_by_name(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile(id="c-1", name="张三"), "resume 1")
    await memory.save_candidate(_make_profile(id="c-2", name="张伟"), "resume 2")
    await memory.save_candidate(_make_profile(id="c-3", name="王五"), "resume 3")

    results = await memory.search_candidates("张")
    names = sorted(p.name for p in results)
    assert names == ["张三", "张伟"]

    all_results = await memory.search_candidates()
    assert len(all_results) == 3


# ─── 历史记忆 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_candidate_history_returns_none_for_unknown(memory: MemoryModule) -> None:
    assert await memory.get_candidate_history("missing") is None


@pytest.mark.asyncio
async def test_get_candidate_history_returns_none_when_no_interviews(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile(), "resume")
    history = await memory.get_candidate_history("c-1")
    assert history is None


@pytest.mark.asyncio
async def test_get_candidate_history_summarises_past_interviews(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile(), "resume")
    session_old = _make_session(interview_id="i-old")
    session_old.metadata.start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await memory.start_interview(session_old)
    await memory.finish_interview(session_old)

    session_new = _make_session(interview_id="i-new")
    session_new.metadata.start_time = datetime(2026, 5, 1, tzinfo=timezone.utc)
    await memory.start_interview(session_new)
    await memory.finish_interview(session_new)

    await memory.save_eval_report(
        EvalReport(
            id="r-new",
            interview_id="i-new",
            dimensions=[],
            overall_score=8.0,
            strengths=["s"],
            weaknesses=["w"],
            recommendation="hire",
            summary="不错的候选人",
            generated_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        )
    )

    history = await memory.get_candidate_history("c-1")
    assert history is not None
    assert len(history.past_interviews) == 2
    assert history.past_interviews[0].interview_id == "i-new"
    assert history.past_interviews[0].overall_score == 8.0
    assert history.past_interviews[0].recommendation == "hire"
    assert history.past_interviews[1].interview_id == "i-old"
    assert history.past_interviews[1].overall_score is None
    assert "张三" in history.history_summary
    assert "历史面试记录" in history.history_summary


# ─── 面试记录 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_interview_persists_rounds_and_tokens(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile(), "resume")
    session = _make_session()
    await memory.start_interview(session)
    await memory.finish_interview(session)

    detail = await memory.get_interview_detail("i-1")
    assert detail is not None
    assert detail.interview_id == "i-1"
    assert detail.candidate_id == "c-1"
    assert detail.end_time is not None
    assert len(detail.rounds) == 2
    assert detail.rounds[0].round_number == 1
    assert detail.rounds[1].llm_suggestion == "深入追问"
    assert detail.recording_paths is None  # no recordings were saved


@pytest.mark.asyncio
async def test_save_interview_is_idempotent_on_rounds(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile(), "resume")
    session = _make_session()
    await memory.start_interview(session)
    await memory.finish_interview(session)
    await memory.finish_interview(session)

    detail = await memory.get_interview_detail("i-1")
    assert detail is not None
    assert len(detail.rounds) == 2  # not duplicated


@pytest.mark.asyncio
async def test_get_interview_detail_missing_returns_none(memory: MemoryModule) -> None:
    assert await memory.get_interview_detail("missing") is None


# ─── 评价报告 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_and_get_eval_report(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile(), "resume")
    session = _make_session()
    await memory.start_interview(session)
    await memory.finish_interview(session)

    report = EvalReport(
        id="r-1",
        interview_id="i-1",
        dimensions=[
            DimensionScore(dimension="算法", score=8.0, comment="ok", evidence=["e"]),
            DimensionScore(dimension="系统设计", score=7.0, comment="ok", evidence=[]),
        ],
        overall_score=7.5,
        strengths=["扎实"],
        weaknesses=["分布式弱"],
        recommendation="hire",
        summary="综合评价",
        generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
    )
    await memory.save_eval_report(report)

    loaded = await memory.get_eval_report("i-1")
    assert loaded is not None
    assert loaded.id == "er-i-1"
    assert loaded.overall_score == 7.5
    assert loaded.recommendation == "hire"
    assert "综合评价" in loaded.summary
    assert [d.dimension for d in loaded.dimensions] == ["算法", "系统设计"]
    assert loaded.strengths == ["扎实"]
    assert loaded.weaknesses == ["分布式弱"]


@pytest.mark.asyncio
async def test_get_eval_report_missing_returns_none(memory: MemoryModule) -> None:
    assert await memory.get_eval_report("missing") is None


# ── S-6 + recovery ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_round_creates_jsonl_wal(memory: MemoryModule) -> None:
    """S-6 回归：append_round 每次写一行 JSON，进程崩溃也能从 WAL 恢复。"""
    session = _make_session()
    await memory.save_candidate(session.candidate, "## 测试简历")
    await memory.start_interview(session)

    r = ConversationRound(round_number=1, interviewer_text="q1", candidate_text="a1")
    await memory.append_round(session.candidate.id, session.id, r)
    await memory.append_round(
        session.candidate.id,
        session.id,
        ConversationRound(round_number=2, interviewer_text="q2", candidate_text="a2"),
    )

    wal = Path(memory._root) / session.candidate.id / "interviews" / session.id / "rounds.jsonl"
    assert wal.exists()
    lines = wal.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_scan_orphan_wal_finds_unfinished_interviews(memory: MemoryModule) -> None:
    """recovery: 未归档的 rounds.jsonl 会被列出供恢复。"""
    session = _make_session(interview_id="i-orphan")
    await memory.save_candidate(session.candidate, "## md")
    await memory.start_interview(session)
    await memory.append_round(
        session.candidate.id,
        session.id,
        ConversationRound(round_number=1, interviewer_text="qa", candidate_text="ab"),
    )

    orphans = await memory.scan_orphan_wal()
    assert len(orphans) == 1
    o = orphans[0]
    assert o["candidate_id"] == session.candidate.id
    assert o["interview_id"] == "i-orphan"
    assert o["round_count"] == 1
    assert o["candidate_name"] == "张三"


@pytest.mark.asyncio
async def test_recover_interview_from_wal_rebuilds_transcript(memory: MemoryModule) -> None:
    """recovery: 从 WAL 重建 transcript.md 并归档 WAL。"""
    session = _make_session(interview_id="i-recover")
    await memory.save_candidate(session.candidate, "## md")
    await memory.start_interview(session)
    for n in (1, 2, 3):
        await memory.append_round(
            session.candidate.id,
            session.id,
            ConversationRound(
                round_number=n,
                interviewer_text=f"q{n}",
                candidate_text=f"a{n}",
            ),
        )

    recovered = await memory.recover_interview_from_wal(session.candidate.id, "i-recover")
    assert recovered == 3

    # transcript.md 已生成
    transcript = (
        Path(memory._root)
        / session.candidate.id
        / "interviews"
        / "i-recover"
        / "transcript.md"
    )
    assert transcript.exists()
    text = transcript.read_text(encoding="utf-8")
    assert "q1" in text and "a3" in text

    # WAL 已归档
    wal = transcript.parent / "rounds.jsonl"
    archived = transcript.parent / "rounds.jsonl.archived"
    assert not wal.exists()
    assert archived.exists()

    # 再扫一次应该没有 orphans
    assert await memory.scan_orphan_wal() == []


@pytest.mark.asyncio
async def test_discard_orphan_wal_removes_file(memory: MemoryModule) -> None:
    session = _make_session(interview_id="i-discard")
    await memory.save_candidate(session.candidate, "## md")
    await memory.start_interview(session)
    await memory.append_round(
        session.candidate.id,
        session.id,
        ConversationRound(round_number=1, interviewer_text="q", candidate_text="a"),
    )

    deleted = await memory.discard_orphan_wal(session.candidate.id, "i-discard")
    assert deleted is True
    wal = (
        Path(memory._root)
        / session.candidate.id
        / "interviews"
        / "i-discard"
        / "rounds.jsonl"
    )
    assert not wal.exists()
