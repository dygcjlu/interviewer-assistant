"""Tests for the Orchestrator session lifecycle and Agent switching."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base import AgentRequest, AgentResponse, BaseAgent
from src.agents.orchestrator import Orchestrator
from src.framework.prompt_builder import AgentConfig
from src.framework.tool_registry import ToolRegistry
from src.models.candidate import CandidateProfile
from src.models.exceptions import SessionError
from src.models.session import (
    ConversationRound,
    InterviewSession,
    InterviewStage,
    SessionMetadata,
)


class _FakeAgent(BaseAgent):
    def __init__(self, name: str) -> None:
        super().__init__(
            config=AgentConfig(name=name, system_prompt=""),
            prompt_builder=None,  # type: ignore[arg-type]
            llm_client=None,  # type: ignore[arg-type]
            tool_registry=ToolRegistry(),
        )
        self.activated = 0
        self.deactivated = 0
        self.last_request: AgentRequest | None = None
        self._trigger: Any = None

    async def on_activate(self, session: InterviewSession) -> None:
        self.activated += 1

    async def on_deactivate(self, session: InterviewSession) -> None:
        self.deactivated += 1

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        self.last_request = request
        return AgentResponse(success=True, data={"name": self.config.name})


class _FakeInterviewAgent(_FakeAgent):
    """Minimal stand-in mimicking InterviewAgent's extra hooks."""

    def __init__(self) -> None:
        super().__init__("interview")
        self._trigger = MagicMock()
        self.attached_sender: Any = None

    @property
    def suggestion_trigger(self) -> Any:
        return self._trigger

    def attach_ws_sender(self, sender: Any) -> None:
        self.attached_sender = sender


def _make_memory_mock() -> MagicMock:
    memory = MagicMock()
    memory.get_candidate = AsyncMock(return_value=None)
    memory.get_candidate_history = AsyncMock(return_value=None)
    memory.save_interview = AsyncMock()
    return memory


def _make_audio_mock() -> MagicMock:
    audio = MagicMock()
    audio.start = AsyncMock()
    audio.stop = AsyncMock()
    audio.pause = AsyncMock()
    audio.resume = AsyncMock()
    return audio


def _make_orchestrator() -> tuple[Orchestrator, _FakeAgent, _FakeInterviewAgent, _FakeAgent, MagicMock, MagicMock]:
    resume_agent = _FakeAgent("resume")
    interview_agent = _FakeInterviewAgent()
    eval_agent = _FakeAgent("eval")
    memory = _make_memory_mock()
    audio = _make_audio_mock()
    # Orchestrator does isinstance(interview_agent, InterviewAgent) check; bypass by
    # subclassing — patch isinstance in the orchestrator module if needed.
    orch = Orchestrator(
        resume_agent=resume_agent,  # type: ignore[arg-type]
        interview_agent=interview_agent,  # type: ignore[arg-type]
        eval_agent=eval_agent,  # type: ignore[arg-type]
        memory_module=memory,
        audio_manager=audio,
    )
    return orch, resume_agent, interview_agent, eval_agent, memory, audio


@pytest.mark.asyncio
async def test_create_session_returns_idle_session() -> None:
    orch, *_ = _make_orchestrator()

    session = await orch.create_session()

    assert session.id
    assert session.stage == InterviewStage.IDLE
    assert session.candidate.id  # non-empty UUID
    assert orch.stage == InterviewStage.IDLE


@pytest.mark.asyncio
async def test_switch_agent_resume_activates_and_updates_stage() -> None:
    orch, resume_agent, *_ = _make_orchestrator()
    await orch.create_session()

    await orch.switch_agent("resume")

    assert orch.active_agent is resume_agent
    assert orch.stage == InterviewStage.RESUME_ANALYSIS
    assert resume_agent.activated == 1


@pytest.mark.asyncio
async def test_switch_agent_eval_precondition_fails_without_rounds() -> None:
    orch, *_ = _make_orchestrator()
    await orch.create_session()

    with pytest.raises(SessionError):
        await orch.switch_agent("eval")


@pytest.mark.asyncio
async def test_switch_agent_eval_succeeds_with_rounds() -> None:
    orch, _resume, _interview, eval_agent, *_ = _make_orchestrator()
    session = await orch.create_session()
    session.rounds.append(
        ConversationRound(round_number=1, interviewer_text="q", candidate_text="a")
    )

    await orch.switch_agent("eval")

    assert orch.active_agent is eval_agent
    assert orch.stage == InterviewStage.EVALUATING


@pytest.mark.asyncio
async def test_switch_agent_unknown_target_raises() -> None:
    orch, *_ = _make_orchestrator()
    await orch.create_session()
    with pytest.raises(SessionError):
        await orch.switch_agent("unknown")


@pytest.mark.asyncio
async def test_switch_agent_without_session_raises() -> None:
    orch, *_ = _make_orchestrator()
    with pytest.raises(SessionError):
        await orch.switch_agent("resume")


@pytest.mark.asyncio
async def test_handle_request_without_active_agent_returns_error() -> None:
    orch, *_ = _make_orchestrator()
    await orch.create_session()

    request = AgentRequest(type="noop", payload={}, session=await orch.get_session())  # type: ignore[arg-type]
    response = await orch.handle_request(request)

    assert response.success is False
    assert response.error is not None


@pytest.mark.asyncio
async def test_handle_request_dispatches_to_active_agent() -> None:
    orch, resume_agent, *_ = _make_orchestrator()
    session = await orch.create_session()
    await orch.switch_agent("resume")

    request = AgentRequest(type="parse_resume", payload={}, session=session)
    response = await orch.handle_request(request)

    assert response.success is True
    assert resume_agent.last_request is not None
    assert resume_agent.last_request.type == "parse_resume"


@pytest.mark.asyncio
async def test_close_session_archives_to_memory() -> None:
    orch, _resume, _interview, _eval, memory, _audio = _make_orchestrator()
    await orch.create_session()
    await orch.switch_agent("resume")

    await orch.close_session()

    memory.save_interview.assert_awaited_once()
    assert await orch.get_session() is None