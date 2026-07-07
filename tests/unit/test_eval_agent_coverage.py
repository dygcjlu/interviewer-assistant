"""Unit tests — EvalAgent question coverage statistics (Task 4)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base import AgentRequest
from src.agents.eval_agent import EvalAgent
from src.framework.prompt_builder import AgentConfig, PromptBuilder
from src.framework.tool_registry import ToolRegistry
from src.llm.protocol import ChatResponse
from src.models.candidate import CandidateProfile
from src.models.session import (
    ConversationRound,
    InterviewSession,
    InterviewStage,
    SessionMetadata,
)
from src.storage.memory_module import MemoryModule
from src.storage.user_memory import UserMemoryStore


def _make_session_with_rounds(
    candidate_id: str = "c-001", n: int = 2
) -> InterviewSession:
    rounds = [
        ConversationRound(
            round_number=i + 1,
            interviewer_text=f"问题 {i+1}",
            candidate_text=f"回答 {i+1}",
            timestamp=datetime.now(),
        )
        for i in range(n)
    ]
    return InterviewSession(
        id="s-eval",
        candidate=CandidateProfile(id=candidate_id, name="张三"),
        rounds=rounds,
        stage=InterviewStage.INTERVIEWING,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id=candidate_id, start_time=datetime.now()),
    )


def _make_eval_agent(
    tmp_path: Path,
    llm_content: str = '{"dimensions": [], "overall_score": 7.5, "strengths": ["表达清晰"], "weaknesses": [], "recommendation": "hire", "summary": "总体良好，面试表现优秀，沟通能力强，技术深度达到岗位要求。"}',
) -> EvalAgent:
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=ChatResponse(content=llm_content))
    # count_tokens 是同步 Protocol 方法；AsyncMock 未显式配置的子属性默认也是
    # AsyncMock，同步调用会拿到未 await 的 coroutine，需显式配置为 MagicMock。
    mock_llm.count_tokens = MagicMock(return_value=100)
    candidates_dir = tmp_path / "candidates"
    candidates_dir.mkdir()
    memory_module = MemoryModule(candidates_dir=str(candidates_dir))

    pb = MagicMock(spec=PromptBuilder)
    pb.build.return_value = []
    registry = ToolRegistry()
    user_memory_path = tmp_path / "USER.md"
    user_memory_path.write_text("")
    user_memory_store = UserMemoryStore(user_memory_path)
    user_memory_store.load()

    config = AgentConfig(name="eval", system_prompt="Eval Agent")
    return EvalAgent(config, pb, mock_llm, registry, memory_module, user_memory_store)


@pytest.mark.unit
class TestEvalAgentQuestionCoverage:
    """测试 EvalAgent 问题覆盖率统计功能"""

    @pytest.mark.asyncio
    async def test_coverage_with_questions_and_some_covered(self, tmp_path):
        """测试有问题清单且部分已覆盖时生成正确覆盖率"""
        # ARRANGE
        valid_json = json.dumps(
            {
                "dimensions": [],
                "overall_score": 7.5,
                "strengths": ["表达清晰"],
                "weaknesses": [],
                "recommendation": "hire",
                "summary": "总体良好。",
            }
        )
        agent = _make_eval_agent(tmp_path, valid_json)

        # 保存候选人
        candidate_id = "c-coverage-test"
        await agent._memory_module.save_candidate(
            CandidateProfile(id=candidate_id, name="李四"), ""
        )

        # 保存问题清单：7 个问题，4 个已覆盖
        questions = [
            {
                "id": "q1",
                "question": "问题1",
                "focus": "技术",
                "covered": True,
                "covered_by": "auto",
            },
            {
                "id": "q2",
                "question": "问题2",
                "focus": "技术",
                "covered": True,
                "covered_by": "auto",
            },
            {"id": "q3", "question": "问题3", "focus": "技术", "covered": False},
            {
                "id": "q4",
                "question": "问题4",
                "focus": "技术",
                "covered": True,
                "covered_by": "auto",
            },
            {"id": "q5", "question": "问题5", "focus": "技术", "covered": False},
            {"id": "q6", "question": "问题6", "focus": "技术", "covered": False},
            {
                "id": "q7",
                "question": "问题7",
                "focus": "技术",
                "covered": True,
                "covered_by": "auto",
            },
        ]
        agent._memory_module.save_questions(candidate_id, questions)

        session = _make_session_with_rounds(candidate_id=candidate_id, n=2)
        req = AgentRequest(type="generate_eval", payload={}, session=session)

        # ACT
        resp = await agent.handle_request(req)

        # ASSERT
        assert resp.success is True
        report = resp.data["report"]
        assert report.question_coverage == "已覆盖 4/7"

    @pytest.mark.asyncio
    async def test_coverage_with_no_questions(self, tmp_path):
        """测试无问题清单时覆盖率为空字符串"""
        # ARRANGE
        valid_json = json.dumps(
            {
                "dimensions": [],
                "overall_score": 7.5,
                "strengths": ["表达清晰"],
                "weaknesses": [],
                "recommendation": "hire",
                "summary": "总体良好。",
            }
        )
        agent = _make_eval_agent(tmp_path, valid_json)

        candidate_id = "c-no-questions"
        await agent._memory_module.save_candidate(
            CandidateProfile(id=candidate_id, name="王五"), ""
        )
        # 不保存问题清单

        session = _make_session_with_rounds(candidate_id=candidate_id, n=2)
        req = AgentRequest(type="generate_eval", payload={}, session=session)

        # ACT
        resp = await agent.handle_request(req)

        # ASSERT
        assert resp.success is True
        report = resp.data["report"]
        assert report.question_coverage == ""

    @pytest.mark.asyncio
    async def test_coverage_all_questions_covered(self, tmp_path):
        """测试全部问题已覆盖时覆盖率为 3/3"""
        # ARRANGE
        valid_json = json.dumps(
            {
                "dimensions": [],
                "overall_score": 8.0,
                "strengths": ["优秀"],
                "weaknesses": [],
                "recommendation": "hire",
                "summary": "全覆盖。",
            }
        )
        agent = _make_eval_agent(tmp_path, valid_json)

        candidate_id = "c-full-coverage"
        await agent._memory_module.save_candidate(
            CandidateProfile(id=candidate_id, name="赵六"), ""
        )

        questions = [
            {"id": "q1", "question": "问题1", "focus": "技术", "covered": True},
            {"id": "q2", "question": "问题2", "focus": "技术", "covered": True},
            {"id": "q3", "question": "问题3", "focus": "技术", "covered": True},
        ]
        agent._memory_module.save_questions(candidate_id, questions)

        session = _make_session_with_rounds(candidate_id=candidate_id, n=2)
        req = AgentRequest(type="generate_eval", payload={}, session=session)

        # ACT
        resp = await agent.handle_request(req)

        # ASSERT
        assert resp.success is True
        report = resp.data["report"]
        assert report.question_coverage == "已覆盖 3/3"

    @pytest.mark.asyncio
    async def test_coverage_no_questions_covered(self, tmp_path):
        """测试无问题被覆盖时覆盖率为 0/5"""
        # ARRANGE
        valid_json = json.dumps(
            {
                "dimensions": [],
                "overall_score": 5.0,
                "strengths": [],
                "weaknesses": ["未回答"],
                "recommendation": "no_hire",
                "summary": "无覆盖。",
            }
        )
        agent = _make_eval_agent(tmp_path, valid_json)

        candidate_id = "c-zero-coverage"
        await agent._memory_module.save_candidate(
            CandidateProfile(id=candidate_id, name="孙七"), ""
        )

        questions = [
            {"id": "q1", "question": "问题1", "focus": "技术", "covered": False},
            {"id": "q2", "question": "问题2", "focus": "技术", "covered": False},
            {"id": "q3", "question": "问题3", "focus": "技术", "covered": False},
            {"id": "q4", "question": "问题4", "focus": "技术", "covered": False},
            {"id": "q5", "question": "问题5", "focus": "技术", "covered": False},
        ]
        agent._memory_module.save_questions(candidate_id, questions)

        session = _make_session_with_rounds(candidate_id=candidate_id, n=2)
        req = AgentRequest(type="generate_eval", payload={}, session=session)

        # ACT
        resp = await agent.handle_request(req)

        # ASSERT
        assert resp.success is True
        report = resp.data["report"]
        assert report.question_coverage == "已覆盖 0/5"
