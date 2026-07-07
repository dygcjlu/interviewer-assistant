"""Unit tests — MainAgent tool-call loop paths.

Covers three execution paths in ``_handle_chat_locked``:

1. Pure text (no tool calls): ``chat_stream`` yields only text deltas
   → ``handle_chat`` yields only ``str`` chunks, no ``dict`` tool-call events.

2. Single tool-call round: first ``chat_stream`` final chunk carries a
   tool call; tool dispatches; follow-up ``self._llm.chat()`` returns text
   → ``handle_chat`` yields ``{"type": "tool_call", ...}`` dict + final str.

3. ``user_facing`` error short-circuit: tool dispatch result contains
   ``{"error": "...", "user_facing": True}``
   → ``handle_chat`` yields the error text, skips further LLM calls.

Note: the ``duplicate_candidate`` short-circuit (Task 4.4) is already
thoroughly tested in ``tests/unit/test_agents.py::TestMainAgentDuplicateCandidateEvent``
at the same abstraction level.  No duplication added here.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.main_agent import MainAgent
from src.framework.tool_registry import ToolRegistry
from src.llm.protocol import ChatResponse, StreamChunk
from src.models.message import FunctionCallInfo, ToolCallInfo
from src.storage.memory_module import MemoryModule
from src.storage.user_memory import UserMemoryStore

# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_tc(name: str, arguments: str = "{}") -> ToolCallInfo:
    return ToolCallInfo(
        id="tc-001",
        type="function",
        function=FunctionCallInfo(name=name, arguments=arguments),
    )


def _make_agent(
    chat_stream_fn,
    *,
    chat_response: ChatResponse | None = None,
    dispatch_result: str = '{"ok": true}',
) -> tuple[MainAgent, MagicMock, MagicMock]:
    """Build a MainAgent with fully scripted LLM and tool mocks.

    Args:
        chat_stream_fn: Async generator function to use as ``llm.chat_stream``.
        chat_response: Return value for ``llm.chat`` (non-streaming follow-up).
            Defaults to an empty-content response with no tool calls.
        dispatch_result: JSON string returned by ``tools.dispatch``.

    Returns:
        ``(agent, llm_mock, tools_mock)`` tuple.
    """
    llm = MagicMock()
    llm.chat_stream = chat_stream_fn
    llm.chat = AsyncMock(
        return_value=chat_response
        if chat_response is not None
        else ChatResponse(content="", tool_calls=None)
    )

    tools = MagicMock(spec=ToolRegistry)
    tools.get_schemas.return_value = []
    tools.dispatch = AsyncMock(return_value=dispatch_result)

    memory = MagicMock(spec=MemoryModule)
    user_memory = MagicMock(spec=UserMemoryStore)
    user_memory.render.return_value = ""

    agent = MainAgent(llm, tools, memory, user_memory)
    return agent, llm, tools


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMainAgentToolLoop:
    # ── Path 1: pure text ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_pure_text_yields_only_str_chunks(self):
        """chat_stream returns text deltas with no tool calls → only str output."""

        async def _stream(messages, tools=None, temperature=0.7, timeout_sec=None):
            for piece in ["你好", "，请问", "有何需要？"]:
                yield StreamChunk(delta=piece)
            yield StreamChunk(
                delta="",
                is_final=True,
                accumulated_content="你好，请问有何需要？",
                tool_calls=None,
            )

        # Arrange
        agent, llm, tools = _make_agent(_stream)

        # Act
        chunks = [c async for c in agent.handle_chat("你好")]

        # Assert
        str_chunks = [c for c in chunks if isinstance(c, str)]
        dict_chunks = [c for c in chunks if isinstance(c, dict)]

        assert dict_chunks == [], "No tool-call dict events expected for pure-text path"
        assert "".join(str_chunks) == "你好，请问有何需要？"
        llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_pure_text_multiple_deltas_are_yielded_incrementally(self):
        """Each text delta from chat_stream is forwarded individually, not batched."""

        async def _stream(messages, tools=None, temperature=0.7, timeout_sec=None):
            for piece in ["A", "B", "C"]:
                yield StreamChunk(delta=piece)
            yield StreamChunk(delta="", is_final=True, accumulated_content="ABC")

        agent, llm, _ = _make_agent(_stream)

        chunks = [c async for c in agent.handle_chat("test")]
        str_chunks = [c for c in chunks if isinstance(c, str)]

        assert len(str_chunks) == 3, "Each delta should be yielded as a separate str"
        assert str_chunks == ["A", "B", "C"]

    # ── Path 2: single tool-call round ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_single_tool_call_yields_event_then_text(self):
        """First stream carries a tool call; follow-up chat() returns text.

        Expected yield sequence:
          {"type": "tool_call", "name": "dispatch_to_agent", "args": ...}
          "已完成简历解析。"
        """
        tc = _make_tc("dispatch_to_agent", '{"agent": "resume", "task": "解析简历"}')

        async def _stream(messages, tools=None, temperature=0.7, timeout_sec=None):
            yield StreamChunk(
                delta="",
                is_final=True,
                accumulated_content="",
                tool_calls=[tc],
            )

        dispatch_result = json.dumps(
            {"type": "parse_done", "markdown_path": "resumes/a.md"},
            ensure_ascii=False,
        )
        follow_up = ChatResponse(content="已完成简历解析。", tool_calls=None)

        # Arrange
        agent, llm, tools = _make_agent(
            _stream, chat_response=follow_up, dispatch_result=dispatch_result
        )

        # Act
        chunks = [c async for c in agent.handle_chat("解析这份简历")]

        # Assert
        dict_chunks = [c for c in chunks if isinstance(c, dict)]
        str_chunks = [c for c in chunks if isinstance(c, str)]

        tool_events = [c for c in dict_chunks if c.get("type") == "tool_call"]
        assert len(tool_events) == 1, "Exactly one tool-call event expected"
        assert tool_events[0]["name"] == "dispatch_to_agent"
        assert tool_events[0]["args"] == '{"agent": "resume", "task": "解析简历"}'

        assert "已完成简历解析" in "".join(str_chunks), "Final LLM text should be yielded"
        tools.dispatch.assert_awaited_once()
        llm.chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_tool_call_dispatches_with_correct_name_and_args(self):
        """tools.dispatch is called with the exact name and arguments from the stream chunk."""
        args = '{"agent": "resume", "task": "生成简报"}'
        tc = _make_tc("dispatch_to_agent", args)

        async def _stream(messages, tools=None, temperature=0.7, timeout_sec=None):
            yield StreamChunk(delta="", is_final=True, tool_calls=[tc])

        agent, _, tools = _make_agent(
            _stream,
            chat_response=ChatResponse(content="完成。", tool_calls=None),
        )
        [_ async for _ in agent.handle_chat("生成简报")]

        tools.dispatch.assert_awaited_once_with("dispatch_to_agent", args)

    # ── Path 3: user_facing error short-circuit ───────────────────────────────

    @pytest.mark.asyncio
    async def test_user_facing_error_yields_error_text_and_stops(self):
        """Tool returns {"error": ..., "user_facing": True} → error text yielded, no chat()."""
        tc = _make_tc("dispatch_to_agent")

        async def _stream(messages, tools=None, temperature=0.7, timeout_sec=None):
            yield StreamChunk(delta="", is_final=True, tool_calls=[tc])

        error_message = "PDF 文件已损坏，无法解析"
        error_result = json.dumps(
            {"error": error_message, "user_facing": True}, ensure_ascii=False
        )

        # Arrange
        agent, llm, tools = _make_agent(_stream, dispatch_result=error_result)

        # Act
        chunks = [c async for c in agent.handle_chat("解析损坏的PDF")]

        # Assert
        str_chunks = [c for c in chunks if isinstance(c, str)]
        dict_chunks = [c for c in chunks if isinstance(c, dict)]

        assert error_message in "".join(
            str_chunks
        ), "User-facing error message should be yielded"
        # The tool_call event IS yielded before dispatch() resolves (frontend "started" signal).
        # What matters is: no follow-up LLM call and no further tool rounds.
        tool_events = [c for c in dict_chunks if c.get("type") == "tool_call"]
        assert len(tool_events) == 1, "Exactly one tool_call event (the failing call)"
        # Critical: no follow-up LLM call after a user_facing error
        llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_facing_error_via_message_key(self):
        """dispatch_to_agent wrapping: {"type": "error", "message": ..., "user_facing": True}."""
        tc = _make_tc("dispatch_to_agent")

        async def _stream(messages, tools=None, temperature=0.7, timeout_sec=None):
            yield StreamChunk(delta="", is_final=True, tool_calls=[tc])

        error_message = "候选人不存在"
        error_result = json.dumps(
            {"type": "error", "message": error_message, "user_facing": True},
            ensure_ascii=False,
        )

        agent, llm, tools = _make_agent(_stream, dispatch_result=error_result)

        chunks = [c async for c in agent.handle_chat("查询候选人")]
        str_chunks = [c for c in chunks if isinstance(c, str)]

        assert error_message in "".join(str_chunks)
        llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_user_facing_error_does_not_short_circuit(self):
        """Tool result with user_facing=False (or absent) proceeds normally to chat()."""
        tc = _make_tc("dispatch_to_agent")

        async def _stream(messages, tools=None, temperature=0.7, timeout_sec=None):
            yield StreamChunk(delta="", is_final=True, tool_calls=[tc])

        # user_facing absent → NOT a short-circuit
        normal_result = json.dumps({"type": "ok", "data": "some result"})
        follow_up = ChatResponse(content="操作完成。", tool_calls=None)

        agent, llm, _ = _make_agent(
            _stream, chat_response=follow_up, dispatch_result=normal_result
        )

        chunks = [c async for c in agent.handle_chat("执行操作")]
        str_chunks = [c for c in chunks if isinstance(c, str)]

        assert "操作完成" in "".join(str_chunks)
        # chat() should have been called (not short-circuited)
        llm.chat.assert_awaited_once()
