"""Tests for src/storage/repositories.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from src.models.evaluation import DimensionScore, EvalReport
from src.models.session import ConversationRound
from src.storage.database import Database
from src.storage.repositories import (
    CandidateRepository,
    EvalReportRepository,
    InterviewRepository,
    RoundRepository,
    TokenUsageRepository,
)


@pytest_asyncio.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


# ─── CandidateRepository ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_candidate_insert_and_get(db: Database) -> None:
    repo = CandidateRepository(db)
    await repo.insert("c-1", "张三", "resume text", '{"email":"a@b.c"}')

    row = await repo.get_by_id("c-1")
    assert row is not None
    assert row["id"] == "c-1"
    assert row["name"] == "张三"
    assert row["resume_text"] == "resume text"
    assert row["profile_json"] == '{"email":"a@b.c"}'
    assert row["created_at"]


@pytest.mark.asyncio
async def test_candidate_get_missing_returns_none(db: Database) -> None:
    repo = CandidateRepository(db)
    assert await repo.get_by_id("missing") is None


@pytest.mark.asyncio
async def test_candidate_search_by_name(db: Database) -> None:
    repo = CandidateRepository(db)
    await repo.insert("c-1", "张三", "", "{}")
    await repo.insert("c-2", "张伟", "", "{}")
    await repo.insert("c-3", "王五", "", "{}")

    results = await repo.search_by_name("张")
    names = sorted(r["name"] for r in results)
    assert names == ["张三", "张伟"]

    results_all = await repo.search_by_name("")
    assert len(results_all) == 3

    results_limit = await repo.search_by_name("", limit=1)
    assert len(results_limit) == 1


@pytest.mark.asyncio
async def test_candidate_update_profile(db: Database) -> None:
    repo = CandidateRepository(db)
    await repo.insert("c-1", "张三", "", '{"v":1}')
    await repo.update_profile("c-1", '{"v":2}')
    row = await repo.get_by_id("c-1")
    assert row is not None
    assert row["profile_json"] == '{"v":2}'


@pytest.mark.asyncio
async def test_candidate_insert_preserves_created_at(db: Database) -> None:
    repo = CandidateRepository(db)
    await repo.insert("c-1", "张三", "", "{}")
    row1 = await repo.get_by_id("c-1")
    assert row1 is not None
    original_created_at = row1["created_at"]

    await repo.insert("c-1", "张三 updated", "new", "{}")
    row2 = await repo.get_by_id("c-1")
    assert row2 is not None
    assert row2["created_at"] == original_created_at
    assert row2["name"] == "张三 updated"


# ─── InterviewRepository ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interview_insert_and_get(db: Database) -> None:
    await CandidateRepository(db).insert("c-1", "张三", "", "{}")
    repo = InterviewRepository(db)
    start = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)
    await repo.insert("i-1", "c-1", start, "[]", "auto")

    row = await repo.get_by_id("i-1")
    assert row is not None
    assert row["id"] == "i-1"
    assert row["candidate_id"] == "c-1"
    assert row["start_time"] == start.isoformat()
    assert row["stage"] == "idle"
    assert row["trigger_mode"] == "auto"
    assert row["end_time"] is None


@pytest.mark.asyncio
async def test_interview_get_by_candidate_orders_desc(db: Database) -> None:
    await CandidateRepository(db).insert("c-1", "张三", "", "{}")
    repo = InterviewRepository(db)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await repo.insert("i-old", "c-1", base, "[]", "auto")
    await repo.insert("i-mid", "c-1", base + timedelta(days=10), "[]", "auto")
    await repo.insert("i-new", "c-1", base + timedelta(days=30), "[]", "auto")

    results = await repo.get_by_candidate("c-1")
    ids = [r["id"] for r in results]
    assert ids == ["i-new", "i-mid", "i-old"]

    limited = await repo.get_by_candidate("c-1", limit=2)
    assert [r["id"] for r in limited] == ["i-new", "i-mid"]


@pytest.mark.asyncio
async def test_interview_update_on_finish(db: Database) -> None:
    await CandidateRepository(db).insert("c-1", "张三", "", "{}")
    repo = InterviewRepository(db)
    start = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)
    await repo.insert("i-1", "c-1", start, "[]", "auto")

    end = start + timedelta(hours=1)
    await repo.update_on_finish(
        "i-1", end, "context summary text", "/path/cand.wav", "/path/intvr.wav"
    )

    row = await repo.get_by_id("i-1")
    assert row is not None
    assert row["end_time"] == end.isoformat()
    assert row["context_summary"] == "context summary text"
    assert row["full_recording_candidate_path"] == "/path/cand.wav"
    assert row["full_recording_interviewer_path"] == "/path/intvr.wav"
    assert row["stage"] == "completed"


# ─── RoundRepository ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_round_insert_returns_id_and_ordered(db: Database) -> None:
    await CandidateRepository(db).insert("c-1", "张三", "", "{}")
    await InterviewRepository(db).insert(
        "i-1", "c-1", datetime.now(timezone.utc), "[]", "auto"
    )
    repo = RoundRepository(db)

    r1 = ConversationRound(round_number=1, interviewer_text="hi", candidate_text="hello")
    r2 = ConversationRound(round_number=2, interviewer_text="q2", candidate_text="a2")

    id1 = await repo.insert("i-1", r1)
    id2 = await repo.insert("i-1", r2)
    assert id1 > 0
    assert id2 > id1

    rows = await repo.get_by_interview("i-1")
    assert [r["round_number"] for r in rows] == [1, 2]
    assert rows[0]["interviewer_text"] == "hi"
    assert rows[1]["candidate_text"] == "a2"


@pytest.mark.asyncio
async def test_round_preserves_timestamp_and_nullable_fields(db: Database) -> None:
    await CandidateRepository(db).insert("c-1", "张三", "", "{}")
    await InterviewRepository(db).insert(
        "i-1", "c-1", datetime.now(timezone.utc), "[]", "auto"
    )
    repo = RoundRepository(db)

    ts = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    r = ConversationRound(
        round_number=1,
        interviewer_text="q",
        candidate_text="a",
        llm_suggestion="ask more",
        interviewer_audio_path="/i.wav",
        candidate_audio_path="/c.wav",
        timestamp=ts,
    )
    await repo.insert("i-1", r)

    rows = await repo.get_by_interview("i-1")
    assert rows[0]["timestamp"] == ts.isoformat()
    assert rows[0]["llm_suggestion"] == "ask more"
    assert rows[0]["candidate_audio_path"] == "/c.wav"


# ─── EvalReportRepository ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eval_report_insert_and_get(db: Database) -> None:
    await CandidateRepository(db).insert("c-1", "张三", "", "{}")
    await InterviewRepository(db).insert(
        "i-1", "c-1", datetime.now(timezone.utc), "[]", "auto"
    )
    repo = EvalReportRepository(db)

    report = EvalReport(
        id="r-1",
        interview_id="i-1",
        dimensions=[
            DimensionScore(
                dimension="系统设计", score=8.0, comment="ok", evidence=["原话1"]
            )
        ],
        overall_score=7.5,
        strengths=["项目经验扎实"],
        weaknesses=["缺乏分布式经验"],
        recommendation="hire",
        summary="综合评价文本",
        generated_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
    )
    await repo.insert(report)

    row = await repo.get_by_interview("i-1")
    assert row is not None
    assert row["id"] == "r-1"
    assert row["recommendation"] == "hire"
    assert row["full_report"] == "综合评价文本"

    import json

    assert json.loads(row["strengths"]) == ["项目经验扎实"]
    assert json.loads(row["weaknesses"]) == ["缺乏分布式经验"]
    scores = json.loads(row["scores_json"])
    assert scores["overall_score"] == 7.5
    assert scores["dimensions"][0]["dimension"] == "系统设计"


@pytest.mark.asyncio
async def test_eval_report_get_missing_returns_none(db: Database) -> None:
    repo = EvalReportRepository(db)
    assert await repo.get_by_interview("nope") is None


# ─── TokenUsageRepository ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_token_usage_insert_and_aggregate(db: Database) -> None:
    await CandidateRepository(db).insert("c-1", "张三", "", "{}")
    await InterviewRepository(db).insert(
        "i-1", "c-1", datetime.now(timezone.utc), "[]", "auto"
    )
    repo = TokenUsageRepository(db)

    await repo.insert("i-1", 1, 100, 50)
    await repo.insert("i-1", 2, 200, 80)
    await repo.insert("i-1", 3, 50, 30)

    prompt, completion = await repo.get_total_by_interview("i-1")
    assert prompt == 350
    assert completion == 160


@pytest.mark.asyncio
async def test_token_usage_empty_returns_zero(db: Database) -> None:
    repo = TokenUsageRepository(db)
    prompt, completion = await repo.get_total_by_interview("missing")
    assert (prompt, completion) == (0, 0)