"""共享 fixtures，所有测试模块均可使用。"""
import pytest


@pytest.fixture
def sample_candidate():
    from src.models.candidate import CandidateProfile
    return CandidateProfile(id="c-test-001", name="张三")


@pytest.fixture
def sample_session(sample_candidate):
    from datetime import datetime
    from src.models.session import (
        InterviewSession, InterviewStage, SessionMetadata
    )
    return InterviewSession(
        id="s-test-001",
        candidate=sample_candidate,
        question_plan=[],
        rounds=[],
        stage=InterviewStage.IDLE,
        context_summary="",
        covered_dimensions=set(),
        metadata=SessionMetadata(
            candidate_id="c-test-001",
            start_time=datetime.now(),
        ),
    )
