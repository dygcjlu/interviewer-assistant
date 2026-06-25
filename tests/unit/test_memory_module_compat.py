"""Unit tests for MemoryModule backward compatibility with EvalReport."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.storage.memory_module import MemoryModule


@pytest.mark.unit
class TestMemoryModuleEvalReportCompatibility:
    """Test that MemoryModule handles old EvalReport data without new fields."""

    @pytest.mark.asyncio
    async def test_get_eval_report_old_data_missing_candidate_id(self, tmp_path):
        """get_eval_report should handle old data without candidate_id field."""
        # Setup: create old-format eval_report.md
        candidates_dir = tmp_path / "candidates"
        candidate_id = "c-001"
        interview_id = "iv-001"

        eval_path = candidates_dir / candidate_id / "interviews" / interview_id / "eval_report.md"
        eval_path.parent.mkdir(parents=True, exist_ok=True)

        # Old format: no candidate_id or question_coverage
        old_format = """---
interview_id: iv-001
overall_score: 8.0
recommendation: hire
generated_at: '2026-06-25T10:00:00'
strengths:
- Good communication
weaknesses:
- Needs more experience
dimensions: []
---
# 面试评价报告 · 张三

## 综合评分：8.0 / 10

## 推荐结论
hire

Strong candidate overall.
"""
        eval_path.write_text(old_format, encoding="utf-8")

        # Test: should load without error
        memory = MemoryModule(candidates_dir=str(candidates_dir))
        report = await memory.get_eval_report(interview_id, candidate_id=candidate_id)

        assert report is not None
        assert report.interview_id == interview_id
        assert report.candidate_id == ""  # Default value
        assert report.question_coverage == ""  # Default value
        assert report.overall_score == 8.0

    @pytest.mark.asyncio
    async def test_get_eval_report_old_data_missing_question_coverage(self, tmp_path):
        """get_eval_report should handle data with candidate_id but no question_coverage."""
        # Setup: create intermediate-format eval_report.md
        candidates_dir = tmp_path / "candidates"
        candidate_id = "c-002"
        interview_id = "iv-002"

        eval_path = candidates_dir / candidate_id / "interviews" / interview_id / "eval_report.md"
        eval_path.parent.mkdir(parents=True, exist_ok=True)

        # Has candidate_id but no question_coverage
        intermediate_format = """---
interview_id: iv-002
candidate_id: c-002
overall_score: 7.5
recommendation: weak_hire
generated_at: '2026-06-25T11:00:00'
strengths:
- Technical skills
weaknesses: []
dimensions: []
---
# 面试评价报告 · 李四

## 综合评分：7.5 / 10

## 推荐结论
weak_hire

Decent candidate.
"""
        eval_path.write_text(intermediate_format, encoding="utf-8")

        # Test: should load without error
        memory = MemoryModule(candidates_dir=str(candidates_dir))
        report = await memory.get_eval_report(interview_id, candidate_id=candidate_id)

        assert report is not None
        assert report.interview_id == interview_id
        assert report.candidate_id == "c-002"  # Preserved from file
        assert report.question_coverage == ""  # Default value
        assert report.overall_score == 7.5

    @pytest.mark.asyncio
    async def test_get_eval_report_new_data_with_both_fields(self, tmp_path):
        """get_eval_report should handle new data with all fields."""
        # Setup: create new-format eval_report.md
        candidates_dir = tmp_path / "candidates"
        candidate_id = "c-003"
        interview_id = "iv-003"

        eval_path = candidates_dir / candidate_id / "interviews" / interview_id / "eval_report.md"
        eval_path.parent.mkdir(parents=True, exist_ok=True)

        # New format: has both fields
        new_format = """---
interview_id: iv-003
candidate_id: c-003
overall_score: 9.0
recommendation: strong_hire
generated_at: '2026-06-25T12:00:00'
strengths:
- Excellent problem solving
weaknesses: []
dimensions: []
question_coverage: 已覆盖 6/8
---
# 面试评价报告 · 王五

## 综合评分：9.0 / 10

## 推荐结论
strong_hire

Outstanding candidate.
"""
        eval_path.write_text(new_format, encoding="utf-8")

        # Test: should load all fields correctly
        memory = MemoryModule(candidates_dir=str(candidates_dir))
        report = await memory.get_eval_report(interview_id, candidate_id=candidate_id)

        assert report is not None
        assert report.interview_id == interview_id
        assert report.candidate_id == "c-003"
        assert report.question_coverage == "已覆盖 6/8"
        assert report.overall_score == 9.0
