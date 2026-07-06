"""Unit tests for EvalReport model extensions - candidate_id and question_coverage fields."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.models.evaluation import EvalReport


@pytest.mark.unit
class TestEvalReportNewFields:
    """Test that EvalReport supports candidate_id and question_coverage fields."""

    def test_eval_report_has_candidate_id_field(self):
        """EvalReport should have a candidate_id field with default empty string."""
        report = EvalReport(
            id="er-001",
            interview_id="iv-001",
            dimensions=[],
            overall_score=8.0,
            strengths=["Good communication"],
            weaknesses=[],
            recommendation="hire",
            summary="Strong candidate",
            generated_at=datetime.now(),
        )
        # Should have candidate_id attribute
        assert hasattr(report, "candidate_id")
        # Default should be empty string
        assert report.candidate_id == ""

    def test_eval_report_has_question_coverage_field(self):
        """EvalReport should have a question_coverage field with default empty string."""
        report = EvalReport(
            id="er-001",
            interview_id="iv-001",
            dimensions=[],
            overall_score=8.0,
            strengths=["Good communication"],
            weaknesses=[],
            recommendation="hire",
            summary="Strong candidate",
            generated_at=datetime.now(),
        )
        # Should have question_coverage attribute
        assert hasattr(report, "question_coverage")
        # Default should be empty string
        assert report.question_coverage == ""

    def test_eval_report_can_set_candidate_id(self):
        """EvalReport should accept candidate_id in constructor."""
        report = EvalReport(
            id="er-001",
            interview_id="iv-001",
            candidate_id="c-123",
            dimensions=[],
            overall_score=8.0,
            strengths=["Good communication"],
            weaknesses=[],
            recommendation="hire",
            summary="Strong candidate",
            generated_at=datetime.now(),
        )
        assert report.candidate_id == "c-123"

    def test_eval_report_can_set_question_coverage(self):
        """EvalReport should accept question_coverage in constructor."""
        report = EvalReport(
            id="er-001",
            interview_id="iv-001",
            question_coverage="已覆盖 4/7",
            dimensions=[],
            overall_score=8.0,
            strengths=["Good communication"],
            weaknesses=[],
            recommendation="hire",
            summary="Strong candidate",
            generated_at=datetime.now(),
        )
        assert report.question_coverage == "已覆盖 4/7"

    def test_eval_report_with_both_new_fields(self):
        """EvalReport should accept both new fields simultaneously."""
        report = EvalReport(
            id="er-001",
            interview_id="iv-001",
            candidate_id="c-456",
            question_coverage="已覆盖 5/10",
            dimensions=[],
            overall_score=7.5,
            strengths=["Technical depth"],
            weaknesses=["Communication"],
            recommendation="weak_hire",
            summary="Decent technical skills",
            generated_at=datetime.now(),
        )
        assert report.candidate_id == "c-456"
        assert report.question_coverage == "已覆盖 5/10"
