"""Tests for src/storage/memory_module.py."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from src.models.candidate import (
    CandidateProfile,
    Education,
    ProjectExperience,
    WorkExperience,
)
from src.models.evaluation import DimensionScore, EvalReport
from src.models.session import (
    ConversationRound,
    InterviewQuestion,
    InterviewSession,
    InterviewStage,
    SessionMetadata,
)
from src.storage.database import Database
from src.storage.memory_module import MemoryModule


@pytest_asyncio.fixture
async def memory() -> MemoryModule:
    db = Database(":memory:")
    await db.initialize()
    try:
        yield MemoryModule(db)
    finally:
        await db.close()


def _make_profile(id: str = "c-1", name: str = "张三") -> CandidateProfile:
    return CandidateProfile(
        id=id,
        name=name,
        email="zhangsan@example.com",
        phone="13800000000",
        education=[Education(school="清华", degree="本科", major="CS", start_year=2018, end_year=2022)],
        work_experience=[WorkExperience(company="ACME", title="SWE", duration="2022-2024", description="d")],
        skills=["Python", "Go"],
        projects=[
            ProjectExperience(
                name="P", role="lead", tech_stack=["Py"], description="d", highlights=["h"]
            )
        ],
        resume_text="raw resume",
        resume_summary="brief summary",
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
        working_notes="表现良好",
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
    cid = await memory.save_candidate(profile)
    assert cid == "c-1"

    loaded = await memory.get_candidate("c-1")
    assert loaded is not None
    assert loaded.id == "c-1"
    assert loaded.name == "张三"
    assert loaded.email == "zhangsan@example.com"
    assert loaded.phone == "13800000000"
    assert loaded.resume_text == "raw resume"
    assert loaded.resume_summary == "brief summary"
    assert loaded.skills == ["Python", "Go"]
    assert len(loaded.education) == 1
    assert loaded.education[0].school == "清华"
    assert loaded.education[0].start_year == 2018
    assert len(loaded.work_experience) == 1
    assert loaded.work_experience[0].company == "ACME"
    assert len(loaded.projects) == 1
    assert loaded.projects[0].tech_stack == ["Py"]
    assert loaded.history_summary is None


@pytest.mark.asyncio
async def test_save_candidate_generates_id_when_empty(memory: MemoryModule) -> None:
    profile = _make_profile(id="", name="王五")
    cid = await memory.save_candidate(profile)
    assert cid.startswith("c-")
    loaded = await memory.get_candidate(cid)
    assert loaded is not None
    assert loaded.name == "王五"


@pytest.mark.asyncio
async def test_get_candidate_missing_returns_none(memory: MemoryModule) -> None:
    assert await memory.get_candidate("missing") is None


@pytest.mark.asyncio
async def test_search_candidates_filters_by_name(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile(id="c-1", name="张三"))
    await memory.save_candidate(_make_profile(id="c-2", name="张伟"))
    await memory.save_candidate(_make_profile(id="c-3", name="王五"))

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
    await memory.save_candidate(_make_profile())
    history = await memory.get_candidate_history("c-1")
    assert history is None


@pytest.mark.asyncio
async def test_get_candidate_history_summarises_past_interviews(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile())
    session_old = _make_session(interview_id="i-old")
    session_old.metadata.start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await memory.save_interview(session_old)

    session_new = _make_session(interview_id="i-new")
    session_new.metadata.start_time = datetime(2026, 5, 1, tzinfo=timezone.utc)
    await memory.save_interview(session_new)

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
    await memory.save_candidate(_make_profile())
    session = _make_session()
    await memory.save_interview(session)

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
    await memory.save_candidate(_make_profile())
    session = _make_session()
    await memory.save_interview(session)
    await memory.save_interview(session)

    detail = await memory.get_interview_detail("i-1")
    assert detail is not None
    assert len(detail.rounds) == 2  # not duplicated


@pytest.mark.asyncio
async def test_get_interview_detail_missing_returns_none(memory: MemoryModule) -> None:
    assert await memory.get_interview_detail("missing") is None


# ─── 评价报告 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_and_get_eval_report(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile())
    await memory.save_interview(_make_session())

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
    assert loaded.id == "r-1"
    assert loaded.overall_score == 7.5
    assert loaded.recommendation == "hire"
    assert loaded.summary == "综合评价"
    assert [d.dimension for d in loaded.dimensions] == ["算法", "系统设计"]
    assert loaded.strengths == ["扎实"]
    assert loaded.weaknesses == ["分布式弱"]


@pytest.mark.asyncio
async def test_get_eval_report_missing_returns_none(memory: MemoryModule) -> None:
    assert await memory.get_eval_report("missing") is None


# ─── 记忆整合 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consolidate_memory_appends_insights(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile())
    session = _make_session()
    await memory.save_interview(session)
    await memory.save_eval_report(
        EvalReport(
            id="r-1",
            interview_id="i-1",
            dimensions=[DimensionScore(dimension="算法", score=8.0, comment="", evidence=[])],
            overall_score=7.5,
            strengths=["s1"],
            weaknesses=["w1"],
            recommendation="hire",
            summary="ok",
            generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
        )
    )

    await memory.consolidate_memory(session)

    candidate_row = await memory._candidates.get_by_id("c-1")  # type: ignore[attr-defined]
    assert candidate_row is not None
    payload = json.loads(candidate_row["profile_json"])
    insights = payload.get("last_interview_insights")
    assert insights is not None
    assert insights["interview_id"] == "i-1"
    assert insights["overall_score"] == 7.5
    assert insights["recommendation"] == "hire"
    assert insights["strengths"] == ["s1"]
    assert insights["dimension_scores"] == {"算法": 8.0}
    # 原有 profile 字段应保留
    assert payload.get("email") == "zhangsan@example.com"
    assert payload.get("skills") == ["Python", "Go"]


@pytest.mark.asyncio
async def test_consolidate_memory_skips_when_no_report(memory: MemoryModule) -> None:
    await memory.save_candidate(_make_profile())
    session = _make_session()
    await memory.save_interview(session)

    await memory.consolidate_memory(session)

    candidate_row = await memory._candidates.get_by_id("c-1")  # type: ignore[attr-defined]
    assert candidate_row is not None
    payload = json.loads(candidate_row["profile_json"])
    assert "last_interview_insights" not in payload