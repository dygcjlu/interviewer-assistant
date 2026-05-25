"""Tests for InterviewController session lifecycle and audio pipeline."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base import AgentRequest, AgentResponse, BaseAgent
from src.agents.interview_controller import InterviewController
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


class _FakeInterviewAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            config=AgentConfig(name="interview", system_prompt=""),
            prompt_builder=None,  # type: ignore[arg-type]
            llm_client=None,  # type: ignore[arg-type]
            tool_registry=ToolRegistry(),
        )
        self.activated = 0
        self.deactivated = 0
        self._trigger: Any = MagicMock()
        self.attached_sender: Any = None
        self.context_manager = None

    async def on_activate(self, session: InterviewSession) -> None:
        self.activated += 1

    async def on_deactivate(self, session: InterviewSession) -> None:
        self.deactivated += 1

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(success=True, data={})

    @property
    def suggestion_trigger(self) -> Any:
        return self._trigger

    def attach_ws_sender(self, sender: Any) -> None:
        self.attached_sender = sender


class _FakeEvalAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            config=AgentConfig(name="eval", system_prompt=""),
            prompt_builder=None,  # type: ignore[arg-type]
            llm_client=None,  # type: ignore[arg-type]
            tool_registry=ToolRegistry(),
        )

    async def on_activate(self, session: InterviewSession) -> None:
        pass

    async def on_deactivate(self, session: InterviewSession) -> None:
        pass

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(success=True, data={})


def _make_memory_mock() -> MagicMock:
    memory = MagicMock()
    memory.get_candidate = AsyncMock(return_value=None)
    memory.get_candidate_history = AsyncMock(return_value=None)
    memory.finish_interview = AsyncMock()
    return memory


def _make_audio_mock() -> MagicMock:
    audio = MagicMock()
    audio.start = AsyncMock()
    audio.stop = AsyncMock()
    audio.pause = AsyncMock()
    audio.transcription_manager = None
    return audio


def _make_controller() -> tuple[InterviewController, _FakeInterviewAgent, _FakeEvalAgent, MagicMock, MagicMock]:
    interview_agent = _FakeInterviewAgent()
    eval_agent = _FakeEvalAgent()
    memory = _make_memory_mock()
    audio = _make_audio_mock()
    controller = InterviewController(
        interview_agent=interview_agent,  # type: ignore[arg-type]
        eval_agent=eval_agent,  # type: ignore[arg-type]
        memory_module=memory,
        audio_manager=audio,
    )
    return controller, interview_agent, eval_agent, memory, audio


@pytest.mark.asyncio
async def test_create_session_returns_idle_session() -> None:
    controller, *_ = _make_controller()

    session = await controller.create_session()

    assert session.id
    assert session.stage == InterviewStage.IDLE
    assert session.candidate.id
    assert controller.stage == InterviewStage.IDLE


@pytest.mark.asyncio
async def test_create_session_with_candidate_id() -> None:
    controller, *_ = _make_controller()

    session = await controller.create_session("existing-candidate")

    assert session.candidate.id == "existing-candidate"


@pytest.mark.asyncio
async def test_start_interview_activates_agent_and_starts_audio() -> None:
    controller, interview_agent, _, _, audio = _make_controller()
    await controller.create_session()

    await controller.start_interview()

    assert controller.stage == InterviewStage.INTERVIEWING
    assert interview_agent.activated == 1
    audio.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_interview_without_session_raises() -> None:
    controller, *_ = _make_controller()

    with pytest.raises(SessionError):
        await controller.start_interview()


@pytest.mark.asyncio
async def test_start_interview_idempotent_when_already_interviewing() -> None:
    controller, interview_agent, *_ = _make_controller()
    await controller.create_session()
    await controller.start_interview()

    # second call should be ignored
    await controller.start_interview()

    assert interview_agent.activated == 1


@pytest.mark.asyncio
async def test_stop_interview_sets_evaluating_with_rounds() -> None:
    controller, *_ = _make_controller()
    session = await controller.create_session()
    await controller.start_interview()
    session.rounds.append(
        ConversationRound(round_number=1, interviewer_text="q", candidate_text="a")
    )

    await controller.stop_interview()

    assert controller.stage == InterviewStage.EVALUATING


@pytest.mark.asyncio
async def test_stop_interview_sets_completed_without_rounds() -> None:
    controller, *_ = _make_controller()
    await controller.create_session()
    await controller.start_interview()

    await controller.stop_interview()

    assert controller.stage == InterviewStage.COMPLETED


@pytest.mark.asyncio
async def test_close_session_saves_to_memory() -> None:
    controller, _, _, memory, _ = _make_controller()
    await controller.create_session()

    await controller.close_session()

    memory.finish_interview.assert_awaited_once()
    assert await controller.get_session() is None


@pytest.mark.asyncio
async def test_interview_agent_property() -> None:
    controller, interview_agent, *_ = _make_controller()

    assert controller.interview_agent is interview_agent


@pytest.mark.asyncio
async def test_eval_agent_property() -> None:
    controller, _, eval_agent, *_ = _make_controller()

    assert controller.eval_agent is eval_agent


@pytest.mark.asyncio
async def test_get_session_info_idle() -> None:
    controller, *_ = _make_controller()

    info = await controller.get_session()

    assert info is None


@pytest.mark.asyncio
async def test_get_session_info_with_session() -> None:
    controller, *_ = _make_controller()
    session = await controller.create_session("cand-1")

    info = await controller.get_session()

    assert info is session
    assert info.id == session.id
    assert info.candidate.id == "cand-1"
    assert len(info.rounds) == 0


def test_attach_detach_ws_sender() -> None:
    controller, interview_agent, *_ = _make_controller()

    async def sender1(msg: dict) -> None:
        pass

    async def sender2(msg: dict) -> None:
        pass

    controller.attach_ws_sender(sender1)
    assert id(sender1) in controller._ws_senders

    controller.attach_ws_sender(sender2)
    assert id(sender2) in controller._ws_senders

    controller.detach_ws_sender(id(sender1))
    assert id(sender1) not in controller._ws_senders
    assert id(sender2) in controller._ws_senders
