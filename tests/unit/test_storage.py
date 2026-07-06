"""Unit tests — storage 模块：MemoryModule CRUD 和辅助函数。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.models.candidate import CandidateProfile
from src.models.evaluation import DimensionScore, EvalReport
from src.models.session import (
    ConversationRound,
    InterviewSession,
    InterviewStage,
    SessionMetadata,
)
from src.storage.memory_module import (
    MemoryModule,
    _build_candidates_index,
    _build_eval_report_md,
    _build_profile_md,
    _build_transcript_md,
    _normalize_inline,
    _parse_dt,
    _parse_frontmatter,
    _render_frontmatter,
)

# ── 辅助 ──────────────────────────────────────────────────────────────────────


def _make_module(tmp_path: Path) -> MemoryModule:
    candidates_dir = tmp_path / "candidates"
    candidates_dir.mkdir()
    return MemoryModule(candidates_dir=str(candidates_dir))


def _make_profile(cid: str = "c-001", name: str = "张三") -> CandidateProfile:
    return CandidateProfile(id=cid, name=name, skills=["Python"])


def _make_session(cid: str = "c-001", session_id: str = "s-001") -> InterviewSession:
    return InterviewSession(
        id=session_id,
        candidate=_make_profile(cid),
        rounds=[],
        stage=InterviewStage.IDLE,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id=cid, start_time=datetime.now()),
    )


def _make_eval_report(interview_id: str = "s-001") -> EvalReport:
    return EvalReport(
        id="eval-001",
        interview_id=interview_id,
        dimensions=[
            DimensionScore(
                dimension="技术深度",
                score=8.0,
                comment="优秀",
                evidence=["提到了分布式"],
            )
        ],
        overall_score=8.0,
        strengths=["系统思维"],
        weaknesses=["缺乏运维经验"],
        recommendation="hire",
        summary="总体良好",
        generated_at=datetime.now(),
    )


# ── 辅助函数单元测试 ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestParseRenderFrontmatter:
    def test_parse_valid_frontmatter(self):
        text = "---\nname: 张三\nage: 28\n---\n正文内容"
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "张三"
        assert meta["age"] == 28
        assert body == "正文内容"

    def test_parse_no_frontmatter_returns_empty_meta(self):
        text = "这是普通文本，没有 frontmatter"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_parse_unclosed_frontmatter_returns_empty_meta(self):
        text = "---\nname: test\n正文（未闭合）"
        meta, body = _parse_frontmatter(text)
        assert meta == {}

    def test_render_creates_yaml_block(self):
        meta = {"name": "李四", "age": 30}
        rendered = _render_frontmatter(meta)
        assert rendered.startswith("---\n")
        assert rendered.endswith("---\n")
        assert "李四" in rendered

    def test_render_parse_roundtrip(self):
        original = {"name": "王五", "skills": ["Go", "K8s"]}
        rendered = _render_frontmatter(original) + "\n正文"
        parsed, body = _parse_frontmatter(rendered)
        assert parsed["name"] == "王五"
        assert "Go" in parsed["skills"]


@pytest.mark.unit
class TestParseDt:
    def test_valid_iso_string(self):
        dt = _parse_dt("2024-01-15T10:30:00")
        assert isinstance(dt, datetime)
        assert dt.year == 2024

    def test_none_returns_none(self):
        assert _parse_dt(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_dt("") is None

    def test_invalid_string_returns_none(self):
        assert _parse_dt("not-a-date") is None


@pytest.mark.unit
class TestNormalizeInline:
    def test_single_line_unchanged(self):
        assert _normalize_inline("单行文本") == "单行文本"

    def test_multiline_becomes_single_line(self):
        text = "第一行\n第二行\n第三行"
        result = _normalize_inline(text)
        assert "\n" not in result
        assert "第一行" in result

    def test_empty_string_returns_empty(self):
        assert _normalize_inline("") == ""

    def test_none_returns_empty(self):
        assert _normalize_inline(None) == ""


@pytest.mark.unit
class TestBuildCandidatesIndex:
    def test_generates_markdown_table(self):
        candidates = [{"id": "c-001", "name": "张三", "created_at": "2024-01-01"}]
        result = _build_candidates_index(candidates)
        assert "张三" in result
        assert "c-001" in result
        assert "candidates" in result  # frontmatter key

    def test_empty_candidates_generates_header_only(self):
        result = _build_candidates_index([])
        assert "候选人目录" in result


@pytest.mark.unit
class TestBuildProfileMd:
    def test_generates_frontmatter_with_required_fields(self):
        profile = _make_profile()
        result = _build_profile_md(profile, "# 简历正文")
        assert "c-001" in result
        assert "张三" in result
        assert "# 简历正文" in result

    def test_optional_fields_included_when_present(self):
        profile = _make_profile()
        profile.email = "test@example.com"
        profile.years_of_experience = 5
        result = _build_profile_md(profile, "")
        assert "test@example.com" in result
        assert "5" in result


@pytest.mark.unit
class TestBuildTranscriptMd:
    def test_generates_transcript_with_rounds(self):
        session = _make_session()
        session.rounds = [
            ConversationRound(
                round_number=1, interviewer_text="请介绍一下", candidate_text="我叫张三"
            )
        ]
        result = _build_transcript_md(session)
        assert "Round 1" in result
        assert "请介绍一下" in result
        assert "我叫张三" in result

    def test_generates_transcript_without_rounds(self):
        session = _make_session()
        result = _build_transcript_md(session)
        assert "面试记录" in result

    def test_includes_llm_suggestion_when_present(self):
        session = _make_session()
        r = ConversationRound(
            round_number=1, interviewer_text="问", candidate_text="答"
        )
        r.llm_suggestion = "可以追问并发设计"
        session.rounds = [r]
        result = _build_transcript_md(session)
        assert "可以追问并发设计" in result


@pytest.mark.unit
class TestBuildEvalReportMd:
    def test_generates_report_with_dimensions(self):
        report = _make_eval_report()
        result = _build_eval_report_md(report, "张三")
        assert "技术深度" in result
        assert "8.0" in result or "8" in result
        assert "hire" in result

    def test_includes_summary(self):
        report = _make_eval_report()
        result = _build_eval_report_md(report, "张三")
        assert "总体良好" in result


# ── MemoryModule CRUD ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMemoryModuleCandidates:
    @pytest.mark.asyncio
    async def test_save_and_get_candidate(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "# 简历")
        retrieved = await module.get_candidate("c-001")
        assert retrieved is not None
        assert retrieved.name == "张三"

    @pytest.mark.asyncio
    async def test_get_nonexistent_candidate_returns_none(self, tmp_path):
        module = _make_module(tmp_path)
        result = await module.get_candidate("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_candidate_updates_index(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        candidates = await module.search_candidates()
        assert any(c.name == "张三" for c in candidates)

    @pytest.mark.asyncio
    async def test_save_multiple_candidates(self, tmp_path):
        module = _make_module(tmp_path)
        await module.save_candidate(_make_profile("c-001", "张三"), "")
        await module.save_candidate(_make_profile("c-002", "李四"), "")
        candidates = await module.search_candidates()
        assert len(candidates) == 2

    @pytest.mark.asyncio
    async def test_search_candidates_by_keyword(self, tmp_path):
        module = _make_module(tmp_path)
        await module.save_candidate(_make_profile("c-001", "Python 工程师"), "")
        await module.save_candidate(_make_profile("c-002", "Go 开发者"), "")
        results = await module.search_candidates(keyword="Python")
        assert len(results) == 1
        assert results[0].name == "Python 工程师"

    @pytest.mark.asyncio
    async def test_search_candidates_pagination(self, tmp_path):
        module = _make_module(tmp_path)
        for i in range(5):
            await module.save_candidate(_make_profile(f"c-{i:03d}", f"候选人{i}"), "")
        results = await module.search_candidates(limit=2, offset=0)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_get_resume_markdown_returns_body(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "# 我的简历\n工作经历：...")
        body = await module.get_resume_markdown("c-001")
        assert "我的简历" in body

    @pytest.mark.asyncio
    async def test_get_resume_markdown_nonexistent_returns_empty(self, tmp_path):
        module = _make_module(tmp_path)
        result = await module.get_resume_markdown("nonexistent")
        assert result == ""

    @pytest.mark.asyncio
    async def test_save_candidate_generates_id_if_missing(self, tmp_path):
        module = _make_module(tmp_path)
        profile = CandidateProfile(id="", name="无 ID 候选人")
        cid = await module.save_candidate(profile, "")
        assert cid != ""
        assert len(cid) > 0

    @pytest.mark.asyncio
    async def test_delete_candidate_removes_from_index(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile("c-del", "待删候选人")
        await module.save_candidate(profile, "")
        await module.delete_candidate("c-del")
        result = await module.get_candidate("c-del")
        assert result is None

    @pytest.mark.asyncio
    async def test_save_candidate_overwrites_existing(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile("c-001", "旧名字")
        await module.save_candidate(profile, "旧简历")
        profile.name = "新名字"
        await module.save_candidate(profile, "新简历")
        retrieved = await module.get_candidate("c-001")
        assert retrieved.name == "新名字"


@pytest.mark.unit
class TestMemoryModuleInterviews:
    @pytest.mark.asyncio
    async def test_start_interview_creates_directory(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        interview_dir = tmp_path / "candidates" / "c-001" / "interviews" / "s-001"
        assert interview_dir.exists()

    @pytest.mark.asyncio
    async def test_start_interview_writes_session_json(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        session_json = (
            tmp_path / "candidates" / "c-001" / "interviews" / "s-001" / "session.json"
        )
        assert session_json.exists()
        import json as _json

        data = _json.loads(session_json.read_text())
        assert data["interview_id"] == "s-001"
        assert data["stage"] == "interviewing"

    @pytest.mark.asyncio
    async def test_get_candidate_by_name(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile("c-byname", "独特名字候选人")
        await module.save_candidate(profile, "")
        result = await module.get_candidate_by_name("独特名字候选人")
        assert result is not None
        assert result.id == "c-byname"

    @pytest.mark.asyncio
    async def test_get_candidate_by_name_not_found_returns_none(self, tmp_path):
        module = _make_module(tmp_path)
        result = await module.get_candidate_by_name("不存在的人")
        assert result is None

    @pytest.mark.asyncio
    async def test_append_round_writes_wal(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        round_ = ConversationRound(
            round_number=1, interviewer_text="问", candidate_text="答"
        )
        await module.append_round("c-001", "s-001", round_)
        wal_path = (
            tmp_path / "candidates" / "c-001" / "interviews" / "s-001" / "rounds.jsonl"
        )
        assert wal_path.exists()
        assert "round_number" in wal_path.read_text(encoding="utf-8")


@pytest.mark.unit
class TestMemoryModuleBrief:
    def test_save_and_get_brief(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        # save_brief/get_brief are sync
        (tmp_path / "candidates" / "c-001").mkdir(parents=True, exist_ok=True)
        module.save_brief("c-001", "面试重点：分布式系统")
        brief = module.get_brief("c-001")
        assert "分布式系统" in brief

    def test_get_brief_nonexistent_returns_empty(self, tmp_path):
        module = _make_module(tmp_path)
        result = module.get_brief("nonexistent")
        assert result == ""


@pytest.mark.unit
class TestMemoryModuleFinishInterview:
    @pytest.mark.asyncio
    async def test_finish_interview_creates_transcript(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        session.rounds.append(
            ConversationRound(
                round_number=1, interviewer_text="问", candidate_text="答"
            )
        )
        await module.finish_interview(session)
        transcript_path = (
            tmp_path / "candidates" / "c-001" / "interviews" / "s-001" / "transcript.md"
        )
        assert transcript_path.exists()
        assert "Round 1" in transcript_path.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_finish_interview_updates_session_json(self, tmp_path):
        import json as _json

        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        await module.finish_interview(session)
        session_json_path = (
            tmp_path / "candidates" / "c-001" / "interviews" / "s-001" / "session.json"
        )
        data = _json.loads(session_json_path.read_text(encoding="utf-8"))
        assert data["stage"] == "completed"


@pytest.mark.unit
class TestMemoryModuleEvalReport:
    @pytest.mark.asyncio
    async def test_save_and_get_eval_report(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        await module.finish_interview(session)
        report = _make_eval_report()
        await module.save_eval_report(report)
        retrieved = await module.get_eval_report("s-001", candidate_id="c-001")
        assert retrieved is not None
        assert retrieved.overall_score == 8.0

    @pytest.mark.asyncio
    async def test_get_eval_report_nonexistent_returns_none(self, tmp_path):
        module = _make_module(tmp_path)
        result = await module.get_eval_report(
            "nonexistent-interview", candidate_id="c-001"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_save_eval_report_orphan_when_no_candidate(self, tmp_path):
        """如果找不到 candidate，应将报告写入 eval_orphans/ 并 raise StorageError。"""
        from src.models.exceptions import StorageError

        module = _make_module(tmp_path)
        report = _make_eval_report(interview_id="orphan-interview")
        with pytest.raises(StorageError):
            await module.save_eval_report(report)
        orphan_path = tmp_path / "candidates" / "eval_orphans" / "orphan-interview.md"
        assert orphan_path.exists()

    @pytest.mark.asyncio
    async def test_save_eval_report_upsert_without_finish(self, tmp_path):
        """save_eval_report 在 finish_interview 未调用（如崩溃场景）时应 upsert interviews index。"""
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        # 故意不调用 finish_interview，模拟崩溃后直接生成评价的场景
        report = _make_eval_report()
        await module.save_eval_report(report)

        interviews = module._read_interviews_index("c-001")
        assert len(interviews) == 1
        entry = interviews[0]
        assert entry["interview_id"] == "s-001"
        assert entry["overall_score"] == 8.0
        assert entry["recommendation"] == "hire"
        assert entry["stage"] == "completed"


@pytest.mark.unit
class TestMemoryModuleGetInterviewDetail:
    @pytest.mark.asyncio
    async def test_get_interview_detail_with_candidate_id(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        session.rounds.append(
            ConversationRound(
                round_number=1, interviewer_text="问", candidate_text="答"
            )
        )
        await module.start_interview(session)
        await module.finish_interview(session)
        detail = await module.get_interview_detail("s-001", candidate_id="c-001")
        assert detail is not None
        assert detail.interview_id == "s-001"
        assert len(detail.rounds) == 1

    @pytest.mark.asyncio
    async def test_get_interview_detail_nonexistent_returns_none(self, tmp_path):
        module = _make_module(tmp_path)
        result = await module.get_interview_detail("nonexistent", candidate_id="c-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_interview_detail_without_candidate_id(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        await module.finish_interview(session)
        detail = await module.get_interview_detail("s-001")
        assert detail is not None
        assert detail.candidate_id == "c-001"


@pytest.mark.unit
class TestMemoryModuleScanOrphanWal:
    @pytest.mark.asyncio
    async def test_scan_orphan_wal_empty_when_none(self, tmp_path):
        module = _make_module(tmp_path)
        result = await module.scan_orphan_wal()
        assert result == []

    @pytest.mark.asyncio
    async def test_scan_orphan_wal_finds_orphan(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        # append a round without finish_interview
        round_ = ConversationRound(
            round_number=1, interviewer_text="问", candidate_text="答"
        )
        await module.append_round("c-001", "s-001", round_)
        orphans = await module.scan_orphan_wal()
        assert len(orphans) == 1
        assert orphans[0]["interview_id"] == "s-001"

    @pytest.mark.asyncio
    async def test_scan_orphan_wal_skips_finished(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        # finish without WAL — should not appear as orphan
        await module.finish_interview(session)
        orphans = await module.scan_orphan_wal()
        assert len(orphans) == 0


@pytest.mark.unit
class TestMemoryModuleDiscard:
    @pytest.mark.asyncio
    async def test_discard_orphan_wal_removes_file(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        round_ = ConversationRound(
            round_number=1, interviewer_text="问", candidate_text="答"
        )
        await module.append_round("c-001", "s-001", round_)
        result = await module.discard_orphan_wal("c-001", "s-001")
        assert result is True

    @pytest.mark.asyncio
    async def test_discard_orphan_wal_returns_false_if_missing(self, tmp_path):
        module = _make_module(tmp_path)
        result = await module.discard_orphan_wal("c-001", "nonexistent")
        assert result is False


@pytest.mark.unit
class TestMemoryModuleCandidateHistory:
    @pytest.mark.asyncio
    async def test_get_candidate_history_none_when_no_interviews(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        result = await module.get_candidate_history("c-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_candidate_history_none_when_no_candidate(self, tmp_path):
        module = _make_module(tmp_path)
        result = await module.get_candidate_history("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_candidate_history_returns_summaries(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        await module.finish_interview(session)
        report = _make_eval_report()
        await module.save_eval_report(report)
        result = await module.get_candidate_history("c-001")
        assert result is not None
        assert len(result.past_interviews) == 1


@pytest.mark.unit
class TestMemoryModuleRebuildIndex:
    @pytest.mark.asyncio
    async def test_rebuild_index_creates_candidates_index(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "# 简历内容")
        session = _make_session()
        await module.start_interview(session)
        await module.finish_interview(session)
        await module.rebuild_index()
        index_path = tmp_path / "candidates" / "index.md"
        assert index_path.exists()
        text = index_path.read_text(encoding="utf-8")
        assert "张三" in text


@pytest.mark.unit
class TestMemoryModuleRecoverWal:
    @pytest.mark.asyncio
    async def test_recover_interview_from_wal(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        round_ = ConversationRound(
            round_number=1, interviewer_text="问", candidate_text="答"
        )
        await module.append_round("c-001", "s-001", round_)
        count = await module.recover_interview_from_wal("c-001", "s-001")
        assert count == 1

    @pytest.mark.asyncio
    async def test_recover_from_empty_wal_returns_zero(self, tmp_path):
        module = _make_module(tmp_path)
        profile = _make_profile()
        await module.save_candidate(profile, "")
        session = _make_session()
        await module.start_interview(session)
        wal_path = (
            tmp_path / "candidates" / "c-001" / "interviews" / "s-001" / "rounds.jsonl"
        )
        wal_path.parent.mkdir(parents=True, exist_ok=True)
        wal_path.write_text("", encoding="utf-8")
        count = await module.recover_interview_from_wal("c-001", "s-001")
        assert count == 0


@pytest.mark.unit
class TestParseTranscript:
    def test_parse_transcript_basic(self):
        from src.storage.memory_module import _parse_transcript

        text = "---\n---\n## Round 1\n**面试官：** 请自我介绍\n**候选人：** 我是张三"
        rounds = _parse_transcript(text)
        assert len(rounds) == 1
        assert rounds[0].interviewer_text == "请自我介绍"
        assert rounds[0].candidate_text == "我是张三"

    def test_parse_transcript_multiple_rounds(self):
        from src.storage.memory_module import _parse_transcript

        text = (
            "---\n---\n"
            "## Round 1\n**面试官：** 问题1\n**候选人：** 回答1\n"
            "## Round 2\n**面试官：** 问题2\n**候选人：** 回答2\n"
        )
        rounds = _parse_transcript(text)
        assert len(rounds) == 2
        assert rounds[1].round_number == 2

    def test_parse_transcript_with_suggestion(self):
        from src.storage.memory_module import _parse_transcript

        text = (
            "---\n---\n"
            "## Round 1\n**面试官：** 问题\n**候选人：** 回答\n**追问建议：** 追问什么"
        )
        rounds = _parse_transcript(text)
        assert rounds[0].llm_suggestion == "追问什么"

    def test_parse_transcript_empty_returns_empty(self):
        from src.storage.memory_module import _parse_transcript

        rounds = _parse_transcript("")
        assert rounds == []
