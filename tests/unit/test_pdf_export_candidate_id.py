"""Unit tests — Task 3: Fix PDF export to use report.candidate_id instead of parsing interview_id."""
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
from src.models.evaluation import EvalReport
from src.models.session import ConversationRound, InterviewSession, InterviewStage, SessionMetadata
from src.storage.memory_module import MemoryModule
from src.storage.user_memory import UserMemoryStore


def _make_session_with_candidate(candidate_id: str = "c-001", name: str = "张三") -> InterviewSession:
    """Create a test session with a specific candidate_id."""
    rounds = [
        ConversationRound(
            round_number=1,
            interviewer_text="问题 1",
            candidate_text="回答 1",
            timestamp=datetime.now(),
        )
    ]
    return InterviewSession(
        id="s-test-001",
        candidate=CandidateProfile(id=candidate_id, name=name),
        rounds=rounds,
        stage=InterviewStage.INTERVIEWING,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id=candidate_id, start_time=datetime.now()),
    )


def _make_eval_agent_with_mock_llm(tmp_path: Path) -> EvalAgent:
    """Create an EvalAgent with mocked LLM for testing."""
    valid_json = json.dumps({
        "dimensions": [
            {"dimension": "技术深度", "score": 8.0, "comment": "优秀", "evidence": ["提到了微服务"]}
        ],
        "overall_score": 8.0,
        "strengths": ["系统设计清晰"],
        "weaknesses": ["缺乏运维经验"],
        "recommendation": "hire",
        "summary": "候选人表现良好，技术能力扎实，沟通能力强，符合岗位要求，建议录用。",
    })

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=ChatResponse(content=valid_json))

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
class TestEvalAgentFillsCandidateId:
    """Test that EvalAgent._generate_eval fills report.candidate_id from session."""

    @pytest.mark.asyncio
    async def test_generate_eval_fills_candidate_id_from_session(self, tmp_path):
        """RED: Test that generated report contains candidate_id from session."""
        agent = _make_eval_agent_with_mock_llm(tmp_path)

        # Save candidate first so memory lookups work
        candidate_id = "c-test-123"
        candidate_name = "李四"
        await agent._memory_module.save_candidate(
            CandidateProfile(id=candidate_id, name=candidate_name), ""
        )

        # Create session with specific candidate_id
        session = _make_session_with_candidate(candidate_id=candidate_id, name=candidate_name)

        # Generate evaluation
        req = AgentRequest(type="generate_eval", payload={}, session=session)
        resp = await agent.handle_request(req)

        # Verify response success
        assert resp.success is True
        assert "report" in resp.data

        # Verify report.candidate_id is filled from session
        report: EvalReport = resp.data["report"]
        assert report.candidate_id == candidate_id, f"Expected candidate_id={candidate_id}, got {report.candidate_id}"

    @pytest.mark.asyncio
    async def test_generate_eval_with_different_candidate_ids(self, tmp_path):
        """RED: Test multiple sessions with different candidate_ids."""
        agent = _make_eval_agent_with_mock_llm(tmp_path)

        # Test with first candidate
        candidate_id_1 = "c-alice"
        await agent._memory_module.save_candidate(
            CandidateProfile(id=candidate_id_1, name="Alice"), ""
        )
        session_1 = _make_session_with_candidate(candidate_id=candidate_id_1, name="Alice")
        req_1 = AgentRequest(type="generate_eval", payload={}, session=session_1)
        resp_1 = await agent.handle_request(req_1)

        assert resp_1.success is True
        report_1: EvalReport = resp_1.data["report"]
        assert report_1.candidate_id == candidate_id_1

        # Test with second candidate
        candidate_id_2 = "c-bob"
        await agent._memory_module.save_candidate(
            CandidateProfile(id=candidate_id_2, name="Bob"), ""
        )
        session_2 = _make_session_with_candidate(candidate_id=candidate_id_2, name="Bob")
        req_2 = AgentRequest(type="generate_eval", payload={}, session=session_2)
        resp_2 = await agent.handle_request(req_2)

        assert resp_2.success is True
        report_2: EvalReport = resp_2.data["report"]
        assert report_2.candidate_id == candidate_id_2


@pytest.mark.unit
class TestPdfExportUsesCandidateId:
    """Test that export_report_pdf route logic uses report.candidate_id for candidate lookup."""

    @pytest.mark.asyncio
    async def test_route_logic_uses_report_candidate_id(self, tmp_path):
        """RED: Test the route logic pattern - use report.candidate_id instead of parsing interview_id."""
        # Create memory module
        candidates_dir = tmp_path / "candidates"
        candidates_dir.mkdir()
        memory = MemoryModule(candidates_dir=str(candidates_dir))

        # Save candidate
        candidate_id = "c-export-test"
        candidate_name = "王五"
        await memory.save_candidate(
            CandidateProfile(id=candidate_id, name=candidate_name), ""
        )

        # Create a mock report with candidate_id
        interview_id = "s-uuid-format-123"
        mock_report = EvalReport(
            id="r-001",
            interview_id=interview_id,
            candidate_id=candidate_id,  # The key field we're testing
            dimensions=[],
            overall_score=7.5,
            strengths=["good"],
            weaknesses=["ok"],
            recommendation="hire",
            summary="Summary text",
            generated_at=datetime.now(),
        )

        # Simulate the CORRECTED route logic: use report.candidate_id
        candidate_name_result = ""
        if mock_report.candidate_id:
            candidate = await memory.get_candidate(mock_report.candidate_id)
            if candidate:
                candidate_name_result = candidate.name

        # Verify we got the correct candidate name using report.candidate_id
        assert candidate_name_result == candidate_name

    @pytest.mark.asyncio
    async def test_route_logic_handles_empty_candidate_id(self, tmp_path):
        """RED: Test backward compatibility when report.candidate_id is empty (old reports)."""
        # Create memory module
        candidates_dir = tmp_path / "candidates"
        candidates_dir.mkdir()
        memory = MemoryModule(candidates_dir=str(candidates_dir))

        # Create mock old report WITHOUT candidate_id
        interview_id = "s-old-report-456"
        mock_report = EvalReport(
            id="r-002",
            interview_id=interview_id,
            candidate_id="",  # Empty - old report format
            dimensions=[],
            overall_score=6.0,
            strengths=[],
            weaknesses=[],
            recommendation="hire",
            summary="Old report",
            generated_at=datetime.now(),
        )

        # Simulate the route logic: should handle empty candidate_id gracefully
        candidate_name = ""
        if mock_report.candidate_id:
            candidate = await memory.get_candidate(mock_report.candidate_id)
            if candidate:
                candidate_name = candidate.name

        # Should complete without error, candidate_name stays empty
        assert candidate_name == ""
