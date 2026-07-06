"""Unit tests — 自动覆盖检测功能（后端触发）。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.candidate import CandidateProfile
from src.models.session import (
    ConversationRound,
    InterviewSession,
    InterviewStage,
    SessionMetadata,
)
from src.tools._context import ToolContext


def _make_session() -> InterviewSession:
    """创建测试用 InterviewSession。"""
    return InterviewSession(
        id="s-001",
        candidate=CandidateProfile(id="c-001", name="张三"),
        rounds=[
            ConversationRound(
                round_number=1,
                interviewer_text="请介绍一下你的 Python 经验",
                candidate_text="我有 3 年 Python 开发经验",
            )
        ],
        stage=InterviewStage.INTERVIEWING,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id="c-001", start_time=datetime.now()),
    )


@pytest.mark.unit
class TestAutoCheckCoverage:
    """测试 _auto_check_coverage 函数。"""

    @pytest.mark.asyncio
    async def test_auto_check_coverage_updates_question_coverage(self):
        """当 LLM 识别出覆盖的问题时，应更新问题覆盖状态。"""
        from src.web.routes import _auto_check_coverage

        # 准备测试数据
        session = _make_session()

        # Mock memory module
        mock_memory = MagicMock()
        mock_memory.get_questions = MagicMock(
            return_value=[
                {
                    "id": "q1",
                    "question": "Python 经验",
                    "focus": "技术背景",
                    "covered": False,
                }
            ]
        )
        mock_memory.update_question_coverage = MagicMock(return_value=True)

        # Mock LLM client
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '["q1"]'
        mock_llm.chat = AsyncMock(return_value=mock_response)

        # 执行函数
        await _auto_check_coverage(
            memory=mock_memory,
            llm_client=mock_llm,
            candidate_id="c-001",
            session=session,
        )

        # 验证调用
        mock_memory.get_questions.assert_called_once_with("c-001")
        mock_llm.chat.assert_awaited_once()
        mock_memory.update_question_coverage.assert_called_once_with(
            "c-001", "q1", True, covered_by="auto"
        )

    @pytest.mark.asyncio
    async def test_auto_check_coverage_skips_when_no_questions(self):
        """当没有问题时，应提前返回。"""
        from src.web.routes import _auto_check_coverage

        session = _make_session()

        mock_memory = MagicMock()
        mock_memory.get_questions = MagicMock(return_value=[])

        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock()

        await _auto_check_coverage(
            memory=mock_memory,
            llm_client=mock_llm,
            candidate_id="c-001",
            session=session,
        )

        # 应该不调用 LLM
        mock_llm.chat.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_check_coverage_skips_when_no_rounds(self):
        """当没有对话轮次时，应提前返回。"""
        from src.web.routes import _auto_check_coverage

        session = _make_session()
        session.rounds = []

        mock_memory = MagicMock()
        mock_memory.get_questions = MagicMock(
            return_value=[
                {
                    "id": "q1",
                    "question": "Python 经验",
                    "focus": "技术背景",
                    "covered": False,
                }
            ]
        )

        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock()

        await _auto_check_coverage(
            memory=mock_memory,
            llm_client=mock_llm,
            candidate_id="c-001",
            session=session,
        )

        # 应该不调用 LLM
        mock_llm.chat.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_check_coverage_silently_handles_exceptions(self):
        """当发生异常时，应静默失败不影响主流程。"""
        from src.web.routes import _auto_check_coverage

        session = _make_session()

        mock_memory = MagicMock()
        mock_memory.get_questions = MagicMock(side_effect=Exception("Database error"))

        mock_llm = MagicMock()

        # 不应抛出异常
        await _auto_check_coverage(
            memory=mock_memory,
            llm_client=mock_llm,
            candidate_id="c-001",
            session=session,
        )


@pytest.mark.unit
class TestDispatchSideEffectWithCoverage:
    """测试 dispatch_to_agent 在生成追问建议后触发覆盖检测。"""

    @pytest.mark.asyncio
    async def test_apply_side_effects_triggers_coverage_check_after_suggestion(self):
        """当 result_type 为 'suggestion' 时，应异步触发覆盖检测。"""

        from src.tools.dispatch_to_agent import _apply_side_effects

        session = _make_session()

        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)

        mock_memory = MagicMock()
        mock_memory.get_questions = MagicMock(
            return_value=[
                {
                    "id": "q1",
                    "question": "Python 经验",
                    "focus": "技术背景",
                    "covered": False,
                }
            ]
        )

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '["q1"]'
        mock_llm.chat = AsyncMock(return_value=mock_response)

        mock_main_agent = MagicMock()
        mock_main_agent._llm = mock_llm

        mock_ctx = ToolContext(
            controller=mock_controller,
            memory_module=mock_memory,
            main_agent=mock_main_agent,
        )

        result = {"type": "suggestion", "content": "追问建议"}

        # Mock asyncio.create_task to capture the coroutine
        with (
            patch("src.tools.dispatch_to_agent.ctx", mock_ctx),
            patch("asyncio.create_task") as mock_create_task,
        ):
            await _apply_side_effects("suggestion", result)

            # 验证创建了异步任务
            mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_side_effects_skips_coverage_check_for_other_types(self):
        """当 result_type 不是 'suggestion' 时，不应触发覆盖检测。"""
        from src.tools.dispatch_to_agent import _apply_side_effects

        session = _make_session()

        mock_controller = MagicMock()
        mock_controller.get_session = AsyncMock(return_value=session)

        mock_ctx = ToolContext(controller=mock_controller)

        result = {"type": "parse_done"}

        with (
            patch("src.tools.dispatch_to_agent.ctx", mock_ctx),
            patch("asyncio.create_task") as mock_create_task,
        ):
            await _apply_side_effects("parse_done", result)

            # 不应创建覆盖检测任务
            mock_create_task.assert_not_called()
