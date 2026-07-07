"""Unit tests — dispatch_to_agent 工具。"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.candidate import CandidateProfile
from src.models.session import (
    InterviewSession,
    InterviewStage,
    SessionMetadata,
)
from src.tools._context import ToolContext
from src.tools.dispatch_to_agent import (
    _apply_side_effects,
    _enrich_task_with_session_context,
    dispatch_to_agent,
)

# ── 辅助 ──────────────────────────────────────────────────────────────────────


def _make_session() -> InterviewSession:
    return InterviewSession(
        id="s-001",
        candidate=CandidateProfile(
            id="c-001", name="张三", resume_pdf="resumes/张三.pdf"
        ),
        rounds=[],
        stage=InterviewStage.IDLE,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id="c-001", start_time=datetime.now()),
    )


# ── dispatch_to_agent ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDispatchToAgent:
    @pytest.mark.asyncio
    async def test_unsupported_agent_returns_error(self):
        mock_ctx = ToolContext()
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            result = await dispatch_to_agent("unknown", "任务")
        data = json.loads(result)
        assert data["type"] == "error"
        assert "unknown" in data["message"]

    @pytest.mark.asyncio
    async def test_returns_error_when_resume_agent_none(self):
        mock_ctx = ToolContext(resume_agent=None, controller=MagicMock())
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            result = await dispatch_to_agent("resume", "解析简历")
        data = json.loads(result)
        assert data["type"] == "error"
        assert "初始化" in data["message"]

    @pytest.mark.asyncio
    async def test_returns_error_when_controller_none(self):
        mock_ctx = ToolContext(resume_agent=MagicMock(), controller=None)
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            result = await dispatch_to_agent("resume", "解析简历")
        data = json.loads(result)
        assert data["type"] == "error"

    @pytest.mark.asyncio
    async def test_dispatches_to_resume_agent_successfully(self):
        mock_resume_agent = MagicMock()
        mock_resume_agent.execute = AsyncMock(
            return_value={
                "type": "parse_done",
                "markdown_path": "resumes/test.md",
                "profile": {},
            }
        )
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=_make_session())
        mock_ctx = ToolContext(
            resume_agent=mock_resume_agent,
            controller=mock_controller,
            memory_module=None,
        )
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            result = await dispatch_to_agent("resume", "解析简历")
        data = json.loads(result)
        assert data["type"] == "parse_done"

    @pytest.mark.asyncio
    async def test_propagates_error_result_from_agent(self):
        mock_resume_agent = MagicMock()
        mock_resume_agent.execute = AsyncMock(
            return_value={"type": "error", "message": "解析失败"}
        )
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=None)
        mock_ctx = ToolContext(
            resume_agent=mock_resume_agent, controller=mock_controller
        )
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            result = await dispatch_to_agent("resume", "解析")
        data = json.loads(result)
        assert data["type"] == "error"

    @pytest.mark.asyncio
    async def test_handles_resume_agent_exception(self):
        mock_resume_agent = MagicMock()
        mock_resume_agent.execute = AsyncMock(side_effect=Exception("unexpected error"))
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=None)
        mock_ctx = ToolContext(
            resume_agent=mock_resume_agent, controller=mock_controller
        )
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            result = await dispatch_to_agent("resume", "任务")
        data = json.loads(result)
        assert data["type"] == "error"


# ── _enrich_task_with_session_context ─────────────────────────────────────────


@pytest.mark.unit
class TestEnrichTask:
    @pytest.mark.asyncio
    async def test_enriches_task_with_session_info(self):
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=_make_session())
        mock_ctx = ToolContext(controller=mock_controller)
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            result = await _enrich_task_with_session_context("原始任务")
        assert "c-001" in result
        assert "张三" in result
        assert "原始任务" in result

    @pytest.mark.asyncio
    async def test_returns_original_task_when_no_session(self):
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=None)
        mock_ctx = ToolContext(controller=mock_controller)
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            result = await _enrich_task_with_session_context("原始任务")
        assert result == "原始任务"

    @pytest.mark.asyncio
    async def test_includes_pdf_path_when_resume_pdf_set(self):
        session = _make_session()
        session.candidate.resume_pdf = "resumes/候选人.pdf"
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)
        mock_ctx = ToolContext(controller=mock_controller)
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            result = await _enrich_task_with_session_context("任务")
        assert "候选人" in result


# ── _apply_side_effects ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestApplySideEffects:
    @pytest.mark.asyncio
    async def test_skips_when_no_session(self):
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=None)
        mock_ctx = ToolContext(controller=mock_controller)
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            # 不应抛出
            await _apply_side_effects("parse_done", {})

    @pytest.mark.asyncio
    async def test_parse_done_updates_candidate_profile(self, tmp_path):
        """判重逻辑前移后，profile 更新只在「写入 markdown 成功」的完整路径里发生
        （原测试用 memory_module=None + 无 markdown_path 验证"提前 mutate"，
        该行为本身正是 Task 4.1 报告识别出的坑，已被本任务修正为延后 mutate；
        因此改为提供有效 markdown_path + memory_module，验证新流程下 profile
        仍然会被正确写入 session.candidate）。"""
        session = _make_session()
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)
        mock_memory = MagicMock()
        mock_memory.save_candidate = AsyncMock()
        mock_memory.get_candidate_by_name = AsyncMock(return_value=None)
        md_file = tmp_path / "test.md"
        md_file.write_text("# 简历正文\n", encoding="utf-8")
        mock_ctx = ToolContext(controller=mock_controller, memory_module=mock_memory)
        result = {
            "profile": {"name": "新名字", "skills": ["Python"]},
            "markdown_path": str(md_file),
        }
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            await _apply_side_effects("parse_done", result)
        assert session.candidate.name == "新名字"
        assert session.candidate.skills == ["Python"]

    @pytest.mark.asyncio
    async def test_brief_done_updates_session_brief(self):
        session = _make_session()
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)
        mock_memory = MagicMock()
        mock_memory.save_brief = MagicMock()
        mock_memory.start_interview = AsyncMock()
        mock_ctx = ToolContext(
            controller=mock_controller, memory_module=mock_memory, main_agent=None
        )
        result = {"brief": "候选人简报内容"}
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            await _apply_side_effects("brief_done", result)
        assert session.interview_brief == "候选人简报内容"
        mock_memory.save_brief.assert_called_once_with("c-001", "候选人简报内容")

    @pytest.mark.asyncio
    async def test_parse_done_empty_markdown_warns(self, tmp_path):
        session = _make_session()
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)
        mock_ctx = ToolContext(controller=mock_controller, memory_module=None)
        # 无 markdown_path，resume_markdown 为空
        result = {"profile": {}, "markdown_path": None}
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            await _apply_side_effects("parse_done", result)
        assert "warning" in result

    @pytest.mark.asyncio
    async def test_parse_done_saves_candidate_with_markdown(self, tmp_path):
        session = _make_session()
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)
        mock_memory = MagicMock()
        mock_memory.save_candidate = AsyncMock()
        mock_memory.get_candidate_by_name = AsyncMock(return_value=None)

        # 创建临时 markdown 文件
        md_file = tmp_path / "test.md"
        md_file.write_text("# 简历内容\n工作经历...", encoding="utf-8")

        mock_ctx = ToolContext(controller=mock_controller, memory_module=mock_memory)
        result = {"profile": {}, "markdown_path": str(md_file)}
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            await _apply_side_effects("parse_done", result)
        mock_memory.save_candidate.assert_awaited_once()
        assert session.candidate.resume_content == "# 简历内容\n工作经历..."

    # ── 判重迁移到真实姓名匹配（Task 4.2）──────────────────────────────────

    @pytest.mark.asyncio
    async def test_parse_done_duplicate_hit_creates_pending_without_mutating_session(
        self, tmp_path
    ):
        """解析出的真实姓名与已持久化候选人（非自身）重名时：
        session.candidate 保持原样未被 mutate、不调用 save_candidate、
        ctx.pending_duplicates 新增一条待决议记录。"""
        session = _make_session()
        original_name = session.candidate.name
        original_skills = list(session.candidate.skills)
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)
        mock_memory = MagicMock()
        mock_memory.save_candidate = AsyncMock()
        existing = CandidateProfile(id="c-existing-999", name="李四")
        mock_memory.get_candidate_by_name = AsyncMock(return_value=existing)

        md_file = tmp_path / "test.md"
        md_file.write_text("# 李四的简历\n工作经历...", encoding="utf-8")

        mock_ctx = ToolContext(controller=mock_controller, memory_module=mock_memory)
        result = {
            "profile": {"name": "李四", "skills": ["Python"]},
            "markdown_path": str(md_file),
        }
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            await _apply_side_effects("parse_done", result)

        # session.candidate 未被 mutate（真实字段前后一致）
        assert session.candidate.name == original_name
        assert session.candidate.skills == original_skills

        mock_memory.save_candidate.assert_not_called()

        assert len(mock_ctx.pending_duplicates) == 1
        pending = next(iter(mock_ctx.pending_duplicates.values()))
        assert pending.session_id == session.id
        assert pending.existing_candidate_id == "c-existing-999"
        assert pending.existing_candidate_name == "李四"
        assert pending.new_profile.name == "李四"
        assert pending.new_profile.skills == ["Python"]
        assert pending.resume_markdown == "# 李四的简历\n工作经历..."

        assert (
            result["duplicate_candidate"]["existing_candidate_id"] == "c-existing-999"
        )
        assert result["duplicate_candidate"]["existing_candidate_name"] == "李四"
        assert result["duplicate_candidate"]["new_name"] == "李四"
        assert "pending_id" in result["duplicate_candidate"]

    @pytest.mark.asyncio
    async def test_parse_done_duplicate_miss_new_name_saves_normally(self, tmp_path):
        """全新姓名（index 中不存在同名候选人）：走原有持久化逻辑，
        save_candidate 被调用一次，不产生 pending。"""
        session = _make_session()
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)
        mock_memory = MagicMock()
        mock_memory.save_candidate = AsyncMock()
        mock_memory.get_candidate_by_name = AsyncMock(return_value=None)

        md_file = tmp_path / "test.md"
        md_file.write_text("# 全新候选人简历\n", encoding="utf-8")

        mock_ctx = ToolContext(controller=mock_controller, memory_module=mock_memory)
        result = {"profile": {"name": "全新候选人"}, "markdown_path": str(md_file)}
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            await _apply_side_effects("parse_done", result)

        assert session.candidate.name == "全新候选人"
        mock_memory.save_candidate.assert_awaited_once()
        assert mock_ctx.pending_duplicates == {}
        assert "duplicate_candidate" not in result

    @pytest.mark.asyncio
    async def test_parse_done_self_overwrite_not_treated_as_duplicate(self, tmp_path):
        """existing.id == session.candidate.id（重新解析自己的简历）时，
        自比较保护应继续生效：不视为重复，走原持久化逻辑。"""
        session = _make_session()  # candidate.id == "c-001"
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)
        mock_memory = MagicMock()
        mock_memory.save_candidate = AsyncMock()
        self_candidate = CandidateProfile(id="c-001", name="张三")
        mock_memory.get_candidate_by_name = AsyncMock(return_value=self_candidate)

        md_file = tmp_path / "test.md"
        md_file.write_text("# 张三的最新简历\n", encoding="utf-8")

        mock_ctx = ToolContext(controller=mock_controller, memory_module=mock_memory)
        result = {"profile": {"name": "张三"}, "markdown_path": str(md_file)}
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            await _apply_side_effects("parse_done", result)

        mock_memory.save_candidate.assert_awaited_once()
        assert mock_ctx.pending_duplicates == {}
        assert "duplicate_candidate" not in result
