"""Unit tests — dispatch_to_agent 工具。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.candidate import CandidateProfile
from src.models.session import ConversationRound, InterviewSession, InterviewStage, SessionMetadata
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
        candidate=CandidateProfile(id="c-001", name="张三", resume_pdf="resumes/张三.pdf"),
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
            return_value={"type": "parse_done", "markdown_path": "resumes/test.md", "profile": {}}
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
        mock_ctx = ToolContext(resume_agent=mock_resume_agent, controller=mock_controller)
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
        mock_ctx = ToolContext(resume_agent=mock_resume_agent, controller=mock_controller)
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
    async def test_parse_done_updates_candidate_profile(self):
        session = _make_session()
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)
        mock_ctx = ToolContext(controller=mock_controller, memory_module=None)
        result = {"profile": {"name": "新名字", "skills": ["Python"]}}
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            await _apply_side_effects("parse_done", result)
        assert session.candidate.name == "新名字"

    @pytest.mark.asyncio
    async def test_brief_done_updates_session_brief(self):
        session = _make_session()
        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)
        mock_memory = MagicMock()
        mock_memory.save_brief = MagicMock()
        mock_memory.start_interview = AsyncMock()
        mock_ctx = ToolContext(controller=mock_controller, memory_module=mock_memory, main_agent=None)
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

        # 创建临时 markdown 文件
        md_file = tmp_path / "test.md"
        md_file.write_text("# 简历内容\n工作经历...", encoding="utf-8")

        mock_ctx = ToolContext(controller=mock_controller, memory_module=mock_memory)
        result = {"profile": {}, "markdown_path": str(md_file)}
        with patch("src.tools.dispatch_to_agent.ctx", mock_ctx):
            await _apply_side_effects("parse_done", result)
        mock_memory.save_candidate.assert_awaited_once()
        assert session.candidate.resume_content == "# 简历内容\n工作经历..."
