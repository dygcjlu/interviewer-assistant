"""Unit tests — EvalAgent：handle_request、_parse_eval_json、_format_rounds。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base import AgentRequest
from src.agents.eval_agent import EvalAgent, _format_rounds, _parse_eval_json
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

# ── 共享 fixtures ────────────────────────────────────────────────────────────


def _make_session_with_rounds(n: int = 2) -> InterviewSession:
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
        candidate=CandidateProfile(id="c-001", name="张三"),
        rounds=rounds,
        stage=InterviewStage.INTERVIEWING,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id="c-001", start_time=datetime.now()),
    )


def _make_eval_agent(
    tmp_path: Path,
    llm_content: str = '{"dimensions": [], "overall_score": 7.5, "strengths": ["表达清晰"], "weaknesses": [], "recommendation": "hire", "summary": "总体良好，面试表现优秀，沟通能力强，技术深度达到岗位要求。"}',
) -> EvalAgent:
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=ChatResponse(content=llm_content))
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


# ── _parse_eval_json ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestParseEvalJson:
    def test_plain_json(self):
        data = _parse_eval_json('{"overall_score": 8.0}')
        assert data["overall_score"] == 8.0

    def test_json_in_code_block(self):
        text = '```json\n{"overall_score": 7.0}\n```'
        data = _parse_eval_json(text)
        assert data["overall_score"] == 7.0

    def test_json_embedded_in_text(self):
        text = '这是评价结果：{"overall_score": 6.0} 以上。'
        data = _parse_eval_json(text)
        assert data["overall_score"] == 6.0

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_eval_json("no json here")

    def test_array_input_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_eval_json("[1, 2, 3]")

    def test_empty_string_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_eval_json("")


# ── _format_rounds ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFormatRounds:
    def test_formats_single_round(self):
        rounds = [
            ConversationRound(
                round_number=1, interviewer_text="你好", candidate_text="您好"
            )
        ]
        result = _format_rounds(rounds)
        assert "第 1 轮" in result
        assert "你好" in result
        assert "您好" in result

    def test_formats_multiple_rounds_with_separator(self):
        rounds = [
            ConversationRound(
                round_number=1, interviewer_text="Q1", candidate_text="A1"
            ),
            ConversationRound(
                round_number=2, interviewer_text="Q2", candidate_text="A2"
            ),
        ]
        result = _format_rounds(rounds)
        assert "第 1 轮" in result
        assert "第 2 轮" in result

    def test_empty_rounds_returns_empty(self):
        result = _format_rounds([])
        assert result == ""


# ── EvalAgent.handle_request ──────────────────────────────────────────────────


@pytest.mark.unit
class TestEvalAgentHandleRequest:
    @pytest.mark.asyncio
    async def test_handle_request_unknown_type_returns_failure(self, tmp_path):
        agent = _make_eval_agent(tmp_path)
        session = _make_session_with_rounds()
        req = AgentRequest(type="unknown", payload={}, session=session)
        resp = await agent.handle_request(req)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_generate_eval_no_rounds_returns_failure(self, tmp_path):
        agent = _make_eval_agent(tmp_path)
        session = _make_session_with_rounds(n=0)
        req = AgentRequest(type="generate_eval", payload={}, session=session)
        resp = await agent.handle_request(req)
        assert resp.success is False
        assert "对话记录" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_generate_eval_with_rounds_returns_success(self, tmp_path):
        valid_json = json.dumps(
            {
                "dimensions": [
                    {
                        "dimension": "技术深度",
                        "score": 8.0,
                        "comment": "优秀",
                        "evidence": ["提到了微服务"],
                    }
                ],
                "overall_score": 8.0,
                "strengths": ["系统设计清晰"],
                "weaknesses": ["缺乏运维经验"],
                "recommendation": "hire",
                "summary": "候选人表现良好，技术能力扎实，沟通能力强，符合岗位要求，建议录用。",
            }
        )
        agent = _make_eval_agent(tmp_path, valid_json)
        # 先保存候选人，以便 save_eval_report 不报错
        await agent._memory_module.save_candidate(
            CandidateProfile(id="c-001", name="张三"), ""
        )
        session = _make_session_with_rounds(n=2)
        req = AgentRequest(type="generate_eval", payload={}, session=session)
        resp = await agent.handle_request(req)
        assert resp.success is True
        assert "report" in (resp.data or {})

    @pytest.mark.asyncio
    async def test_on_activate_does_not_raise(self, tmp_path):
        agent = _make_eval_agent(tmp_path)
        session = _make_session_with_rounds()
        await agent.on_activate(session)

    @pytest.mark.asyncio
    async def test_on_deactivate_does_not_raise(self, tmp_path):
        agent = _make_eval_agent(tmp_path)
        session = _make_session_with_rounds()
        await agent.on_deactivate(session)

    @pytest.mark.asyncio
    async def test_generate_eval_invalid_json_returns_failure(self, tmp_path):
        agent = _make_eval_agent(tmp_path, "这不是 JSON 数据，只有纯文本")
        session = _make_session_with_rounds(n=2)
        req = AgentRequest(type="generate_eval", payload={}, session=session)
        resp = await agent.handle_request(req)
        # LLM 两次（retry）都返回无效 JSON，应该 failure
        assert not resp.success or "report" in (resp.data or {})
