"""Tests for BaseAgent._run_with_tools."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from src.agents.base import AgentRequest, AgentResponse, BaseAgent
from src.framework.prompt_builder import AgentConfig
from src.framework.tool_registry import ToolRegistry
from src.llm.protocol import ChatResponse
from src.models.candidate import CandidateProfile
from src.models.exceptions import LLMResponseError
from src.models.message import FunctionCallInfo, Message, ToolCallInfo
from src.models.session import InterviewSession, InterviewStage, SessionMetadata


class _StubLLM:
    """LLM stub that emits a scripted sequence of ChatResponse objects."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=0.7, timeout_sec=None):  # noqa: ANN001
        self.calls += 1
        if not self._responses:
            raise AssertionError("StubLLM exhausted")
        return self._responses.pop(0)

    async def chat_stream(self, messages, temperature=0.7, timeout_sec=None):  # noqa: ANN001
        raise NotImplementedError

    def count_tokens(self, messages) -> int:  # noqa: ANN001
        return 0


class _NoopAgent(BaseAgent):
    """Concrete subclass to test the abstract base."""

    async def on_activate(self, session: InterviewSession) -> None:
        return None

    async def on_deactivate(self, session: InterviewSession) -> None:
        return None

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(success=True)


def _make_agent(
    llm: _StubLLM, tool_registry: ToolRegistry | None = None
) -> _NoopAgent:
    registry = tool_registry or ToolRegistry()
    config = AgentConfig(name="test", system_prompt="hi", tool_names=[])
    return _NoopAgent(
        config=config,
        prompt_builder=None,  # type: ignore[arg-type]
        llm_client=llm,
        tool_registry=registry,
    )


@pytest.mark.asyncio
async def test_run_with_tools_returns_content_on_no_tool_calls() -> None:
    llm = _StubLLM([ChatResponse(content="hello world", tool_calls=None)])
    agent = _make_agent(llm)

    result = await agent._run_with_tools([Message(role="user", content="hi")])

    assert result == "hello world"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_run_with_tools_executes_tool_and_continues() -> None:
    registry = ToolRegistry()

    @registry.register(description="echo input")
    async def echo(text: str) -> str:
        return f"echo:{text}"

    tool_call = ToolCallInfo(
        id="tc-1",
        function=FunctionCallInfo(name="echo", arguments='{"text": "abc"}'),
    )
    llm = _StubLLM(
        [
            ChatResponse(content="", tool_calls=[tool_call]),
            ChatResponse(content="final", tool_calls=None),
        ]
    )
    agent = _make_agent(llm, registry)
    agent.config.tool_names = ["echo"]

    messages: list[Message] = [Message(role="user", content="please")]
    result = await agent._run_with_tools(messages)

    assert result == "final"
    assert llm.calls == 2
    # The tool result message should have been appended
    assert any(m.role == "tool" and "echo:abc" in (m.content or "") for m in messages)


@pytest.mark.asyncio
async def test_run_with_tools_raises_after_max_rounds() -> None:
    # LLM always asks for another tool call → exceeds rounds
    tool_call = ToolCallInfo(
        id="tc-loop",
        function=FunctionCallInfo(name="missing", arguments="{}"),
    )
    looping_response = ChatResponse(content="", tool_calls=[tool_call])
    llm = _StubLLM([looping_response] * 10)
    agent = _make_agent(llm)

    with pytest.raises(LLMResponseError):
        await agent._run_with_tools(
            [Message(role="user", content="loop")], max_tool_rounds=2
        )