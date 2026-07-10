"""Unit tests — UI 纯函数辅助工具（建议清洗、推荐枚举映射、覆盖判定 prompt）。"""

from __future__ import annotations

import pytest

from src.web.routes import _build_coverage_prompt
from src.web.ui import _clean_suggestion_text, _recommendation_display


@pytest.mark.unit
class TestCleanSuggestionText:
    def test_strips_whitespace(self):
        assert _clean_suggestion_text("  你好  ") == "你好"

    def test_removes_wrapping_cn_quotes(self):
        assert _clean_suggestion_text("“这个方案的瓶颈在哪？”") == "这个方案的瓶颈在哪？"

    def test_removes_wrapping_ascii_quotes(self):
        assert _clean_suggestion_text('"能展开说说吗"') == "能展开说说吗"

    def test_removes_corner_brackets(self):
        assert _clean_suggestion_text("「追问细节」") == "追问细节"

    def test_keeps_inner_quotes(self):
        text = '你提到"降级"，具体怎么做的？'
        assert _clean_suggestion_text(text) == text

    def test_empty_string(self):
        assert _clean_suggestion_text("") == ""

    def test_single_quote_char_not_stripped(self):
        assert _clean_suggestion_text('"') == '"'


@pytest.mark.unit
class TestRecommendationDisplay:
    def test_hire_maps_to_chinese(self):
        label, color = _recommendation_display("hire")
        assert label == "建议录用"
        assert color == "positive"

    def test_strong_hire(self):
        label, _ = _recommendation_display("strong_hire")
        assert label == "强烈建议录用"

    def test_no_hire(self):
        label, color = _recommendation_display("no_hire")
        assert label == "不建议录用"
        assert color == "negative"

    def test_case_insensitive(self):
        label, _ = _recommendation_display("HIRE")
        assert label == "建议录用"

    def test_unknown_value_passthrough(self):
        label, color = _recommendation_display("自定义建议")
        assert label == "自定义建议"
        assert color == "grey"


@pytest.mark.unit
class TestBuildCoveragePrompt:
    def test_contains_round_text_and_questions(self):
        uncovered = [
            {"id": "q1", "question": "介绍下 Redis 缓存穿透", "focus": "缓存"},
            {"id": "q2", "question": "MySQL 索引原理", "focus": "数据库"},
        ]
        prompt = _build_coverage_prompt("面试官: 你好\n候选人: 你好", uncovered)
        assert "面试官: 你好" in prompt
        assert "[q1]" in prompt
        assert "[q2]" in prompt
        assert "缓存" in prompt

    def test_uses_lenient_criteria(self):
        prompt = _build_coverage_prompt("对话", [{"id": "a", "question": "q", "focus": "f"}])
        assert "宽松" in prompt
        assert "不要求" in prompt
