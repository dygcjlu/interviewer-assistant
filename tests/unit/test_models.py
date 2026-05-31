"""Unit tests — 数据模型结构与不变量。"""
from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

from src.models.candidate import CandidateProfile, update_candidate_from_data
from src.models.evaluation import DimensionScore, EvalReport
from src.models.session import (
    ConversationRound,
    InterviewSession,
    InterviewStage,
    SessionMetadata,
)


# ── CandidateProfile ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_candidate_profile_minimal_fields():
    """只需 id 和 name 即可创建候选人档案。"""
    candidate = CandidateProfile(id="c-001", name="张三")
    assert candidate.id == "c-001"
    assert candidate.name == "张三"
    assert candidate.email is None
    assert candidate.skills == []


@pytest.mark.unit
def test_candidate_profile_full_fields():
    candidate = CandidateProfile(
        id="c-002",
        name="李四",
        email="li@example.com",
        phone="13800000000",
        age=28,
        skills=["Python", "Go"],
        years_of_experience=5,
        current_position="后端工程师",
    )
    assert candidate.skills == ["Python", "Go"]
    assert candidate.years_of_experience == 5


@pytest.mark.unit
def test_update_candidate_from_data_overwrites_fields():
    candidate = CandidateProfile(id="c-003", name="原始名")
    update_candidate_from_data(candidate, {"name": "新名字", "skills": ["Java", "K8s"]})
    assert candidate.name == "新名字"
    assert candidate.skills == ["Java", "K8s"]


@pytest.mark.unit
def test_update_candidate_from_data_ignores_non_dict():
    candidate = CandidateProfile(id="c-004", name="不变")
    update_candidate_from_data(candidate, ["not", "a", "dict"])
    assert candidate.name == "不变"


@pytest.mark.unit
def test_update_candidate_from_data_invalid_age_skipped():
    candidate = CandidateProfile(id="c-005", name="测试")
    update_candidate_from_data(candidate, {"age": "not-a-number"})
    assert candidate.age is None


# ── InterviewStage ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_interview_stage_values():
    assert InterviewStage.IDLE.value == "idle"
    assert InterviewStage.INTERVIEWING.value == "interviewing"
    assert InterviewStage.EVALUATING.value == "evaluating"
    assert InterviewStage.COMPLETED.value == "completed"


@pytest.mark.unit
def test_interview_stage_is_str_enum():
    """InterviewStage 继承 str，可直接与字符串比较。"""
    assert InterviewStage.IDLE == "idle"


# ── ConversationRound ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_conversation_round_required_fields():
    round_ = ConversationRound(
        round_number=1,
        interviewer_text="你好",
        candidate_text="你好，我叫张三",
    )
    assert round_.round_number == 1
    assert round_.llm_suggestion is None


@pytest.mark.unit
def test_conversation_round_has_timestamp():
    round_ = ConversationRound(round_number=1, interviewer_text="", candidate_text="")
    assert isinstance(round_.timestamp, datetime)


# ── InterviewSession ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_interview_session_initial_stage():
    session = InterviewSession(
        id="s-001",
        candidate=CandidateProfile(id="c-001", name="张三"),
        rounds=[],
        stage=InterviewStage.IDLE,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id="c-001", start_time=datetime.now()),
    )
    assert session.stage == InterviewStage.IDLE
    assert session.rounds == []


@pytest.mark.unit
def test_interview_session_rounds_accumulate():
    session = InterviewSession(
        id="s-002",
        candidate=CandidateProfile(id="c-001", name="张三"),
        rounds=[],
        stage=InterviewStage.INTERVIEWING,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id="c-001", start_time=datetime.now()),
    )
    r1 = ConversationRound(round_number=1, interviewer_text="问", candidate_text="答")
    session.rounds.append(r1)
    assert len(session.rounds) == 1
    assert session.rounds[0].round_number == 1


# ── EvalReport ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_eval_report_structure():
    report = EvalReport(
        id="eval-001",
        interview_id="interview-001",
        dimensions=[
            DimensionScore(
                dimension="系统设计",
                score=8.0,
                comment="表现优秀",
                evidence=["我设计过分布式缓存"],
            )
        ],
        overall_score=7.5,
        strengths=["系统思维清晰"],
        weaknesses=["缺乏运维经验"],
        recommendation="hire",
        summary="总体评价良好",
        generated_at=datetime.now(),
    )
    assert report.overall_score == 7.5
    assert report.recommendation == "hire"
    assert len(report.dimensions) == 1
    assert report.dimensions[0].score == 8.0
    assert "我设计过分布式缓存" in report.dimensions[0].evidence


@pytest.mark.unit
def test_dimension_score_evidence_list():
    ds = DimensionScore(dimension="编程能力", score=9.0, comment="代码质量高", evidence=[])
    assert isinstance(ds.evidence, list)


@pytest.mark.unit
def test_eval_report_is_dataclass():
    assert dataclasses.is_dataclass(EvalReport)
    assert dataclasses.is_dataclass(DimensionScore)
