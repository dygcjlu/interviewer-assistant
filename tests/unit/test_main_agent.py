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

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.main_agent import (
    _HISTORY_LIMIT,
    _NUDGE_INTERVAL,
    MainAgent,
    _extract_duplicate_candidate_event,
    _extract_user_facing_error,
)
from src.framework.tool_registry import ToolRegistry
from src.llm.protocol import ChatResponse, StreamChunk
from src.models.message import FunctionCallInfo, Message, ToolCallInfo
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


# ── Helpers for Tasks 6.2 / 6.3 ──────────────────────────────────────────────


async def _pure_text_stream(messages, tools=None, temperature=0.7, timeout_sec=None):
    """Reusable pure-text stream: yields one delta then a final chunk (no tool calls)."""
    yield StreamChunk(delta="ok")
    yield StreamChunk(delta="", is_final=True, accumulated_content="ok", tool_calls=None)


def _minimal_agent() -> MainAgent:
    """Create a MainAgent instance for direct method tests (no handle_chat needed)."""
    agent, _, _ = _make_agent(_pure_text_stream)
    return agent


# ── Task 6.2 — _trim_history boundary tests ───────────────────────────────────


@pytest.mark.unit
class TestMainAgentTrimHistory:
    def test_no_trim_when_history_below_limit(self):
        """History shorter than _HISTORY_LIMIT is left entirely unchanged."""
        agent = _minimal_agent()
        msgs = [Message(role="user", content=f"msg {i}") for i in range(5)]
        agent._history = list(msgs)
        agent._trim_history()

        assert agent._history == msgs, "Short history must not be modified"
        assert len(agent._history) == 5

    def test_no_trim_at_exact_limit(self):
        """History of exactly _HISTORY_LIMIT messages is left unchanged."""
        agent = _minimal_agent()
        msgs = [Message(role="user", content=f"msg {i}") for i in range(_HISTORY_LIMIT)]
        agent._history = list(msgs)
        agent._trim_history()

        assert agent._history == msgs
        assert len(agent._history) == _HISTORY_LIMIT

    def test_trim_drops_orphan_tool_at_head(self):
        """After truncation, a tool message at the head (whose assistant tool_call was cut
        off) is dropped, producing a history shorter than _HISTORY_LIMIT."""
        agent = _minimal_agent()
        tc = _make_tc("manage_user_memory")
        # Position 0: assistant with tool_call → will be cut by truncation to last 24
        # Position 1: tool response for that call → becomes orphaned head after truncation
        # Positions 2…_HISTORY_LIMIT: regular user messages to reach _HISTORY_LIMIT + 1 total
        msgs: list[Message] = [
            Message(role="assistant", content=None, tool_calls=[tc]),
            Message(role="tool", content='{"ok":true}', tool_call_id="tc-001"),
        ]
        msgs += [
            Message(role="user", content=f"msg {i}") for i in range(_HISTORY_LIMIT - 1)
        ]
        # Total: _HISTORY_LIMIT + 1 messages; last 24 start with the tool message
        assert len(msgs) == _HISTORY_LIMIT + 1
        agent._history = msgs

        agent._trim_history()

        assert agent._history[0].role != "tool", "Orphan tool message must be dropped from head"
        assert len(agent._history) < _HISTORY_LIMIT, (
            "Dropping the orphan should reduce length below _HISTORY_LIMIT"
        )

    def test_trim_preserves_exactly_limit_when_no_orphan(self):
        """When no orphan tool at head, trim leaves exactly _HISTORY_LIMIT messages."""
        agent = _minimal_agent()
        # All user messages: no orphan after truncation
        msgs = [
            Message(role="user", content=f"msg {i}") for i in range(_HISTORY_LIMIT + 1)
        ]
        agent._history = msgs

        agent._trim_history()

        assert len(agent._history) == _HISTORY_LIMIT
        assert agent._history[0].content == "msg 1"  # msg 0 was cut
        assert agent._history[-1].content == f"msg {_HISTORY_LIMIT}"


# ── Task 6.3 — Memory nudge trigger tests ────────────────────────────────────


@pytest.mark.unit
class TestMainAgentNudgeTrigger:
    @pytest.mark.asyncio
    async def test_nudge_triggers_background_review_after_interval_rounds(self):
        """After exactly _NUDGE_INTERVAL pure-text rounds, _background_memory_review
        is scheduled (via asyncio.create_task) and invoked once."""
        agent, _, _ = _make_agent(_pure_text_stream)
        agent._background_memory_review = AsyncMock(return_value=None)

        for _ in range(_NUDGE_INTERVAL):
            async for _chunk in agent.handle_chat("hello"):
                pass

        # Yield to the event loop so the scheduled task can execute.
        await asyncio.sleep(0)

        assert agent._nudge_task is not None, "_nudge_task must be assigned after interval"
        agent._background_memory_review.assert_called_once()

    @pytest.mark.asyncio
    async def test_nudge_does_not_trigger_when_memory_tool_called(self):
        """When manage_user_memory is called in the triggering round, the nudge
        counter/flag resets and no background review task is created that round."""
        tc = _make_tc("manage_user_memory", '{"action": "add", "key": "k", "value": "v"}')

        async def _memory_tool_stream(messages, tools=None, temperature=0.7, timeout_sec=None):
            yield StreamChunk(delta="", is_final=True, accumulated_content="", tool_calls=[tc])

        agent, _, _ = _make_agent(
            _memory_tool_stream,
            chat_response=ChatResponse(content="已保存。", tool_calls=None),
        )
        agent._background_memory_review = AsyncMock(return_value=None)
        # Pre-advance counter to one below the trigger threshold.
        agent._turns_since_nudge = _NUDGE_INTERVAL - 1

        async for _chunk in agent.handle_chat("记住这个偏好"):
            pass
        await asyncio.sleep(0)

        # tool_called_memory path must reset both flag and counter
        assert agent._should_nudge is False, "_should_nudge must be reset when memory tool ran"
        assert agent._turns_since_nudge == 0, "_turns_since_nudge must be reset to 0"
        # Crucially, no background review task should have been created
        assert agent._nudge_task is None, "No nudge task when memory tool already ran this round"
        agent._background_memory_review.assert_not_called()


# ── Task 6.5 — Helper functions and simple method coverage ───────────────────


@pytest.mark.unit
class TestExtractUserFacingError:
    def test_returns_none_for_invalid_json(self):
        """Malformed JSON that contains 'user_facing' keyword returns None without raising."""
        result = _extract_user_facing_error('{"user_facing": True, broken}')
        assert result is None

    def test_returns_none_when_user_facing_false(self):
        """Valid JSON dict with user_facing=False is not an error and returns None."""
        result = _extract_user_facing_error(
            json.dumps({"error": "something", "user_facing": False})
        )
        assert result is None

    def test_returns_none_for_non_dict_json(self):
        """Valid JSON that is not a dict (e.g. a list containing 'user_facing') returns None."""
        result = _extract_user_facing_error('["user_facing", true]')
        assert result is None


@pytest.mark.unit
class TestExtractDuplicateCandidateEvent:
    def test_returns_none_for_invalid_json(self):
        """Malformed JSON that contains 'duplicate_candidate' keyword returns None."""
        result = _extract_duplicate_candidate_event('{"duplicate_candidate": broken}')
        assert result is None

    def test_returns_none_for_non_dict_json(self):
        """Valid JSON array (not a dict) returns None."""
        result = _extract_duplicate_candidate_event('["duplicate_candidate"]')
        assert result is None

    def test_returns_none_when_dup_value_is_not_dict(self):
        """duplicate_candidate value that is not a dict (e.g. a string) returns None."""
        result = _extract_duplicate_candidate_event(
            json.dumps({"duplicate_candidate": "only_a_string"})
        )
        assert result is None


@pytest.mark.unit
class TestMainAgentSimpleMethods:
    def test_reload_user_memory_refreshes_layer2_and_clears_cache(self):
        """reload_user_memory() re-renders from store, updates _layer2_user_memory,
        and invalidates the cached system prompt."""
        agent = _minimal_agent()
        agent._user_memory_store.render.return_value = "岗位：后端工程师"
        agent._cached_system_prompt = "stale cached value"

        agent.reload_user_memory()

        assert agent._layer2_user_memory == "岗位：后端工程师"
        assert agent._cached_system_prompt is None

    def test_clear_candidate_context_blanks_layer3_and_invalidates_cache(self):
        """clear_candidate_context() resets _layer3_candidate to '' and clears cache."""
        agent = _minimal_agent()
        agent._layer3_candidate = "some prior candidate info"
        agent._cached_system_prompt = "stale cached value"

        agent.clear_candidate_context()

        assert agent._layer3_candidate == ""
        assert agent._cached_system_prompt is None

    def test_set_candidate_context_includes_all_optional_fields(self):
        """set_candidate_context() appends position, experience, skills, resume, brief,
        and history_summary when they are all provided."""
        from src.models.candidate import CandidateProfile

        agent = _minimal_agent()
        profile = CandidateProfile(
            id="c001",
            name="张三",
            current_position="后端工程师",
            years_of_experience=5,
            skills=["Python", "Go", "Redis"],
            resume_content="工作经历：ABC 公司",
        )

        agent.set_candidate_context(
            profile,
            interview_brief="面试简报：重点考察系统设计",
            history_summary="上次面试表现良好",
        )

        ctx = agent._layer3_candidate
        assert "后端工程师" in ctx
        assert "5 年" in ctx
        assert "Python" in ctx
        assert "工作经历" in ctx
        assert "面试简报" in ctx
        assert "历史面试记录" in ctx
        assert agent._cached_system_prompt is None

    def test_build_system_prompt_includes_user_memory_section_when_non_empty(self):
        """_build_system_prompt() includes the '面试官偏好与岗位要求' section
        only when _layer2_user_memory is non-empty."""
        agent = _minimal_agent()
        agent._layer2_user_memory = "岗位：前端工程师\n技术栈：React"
        agent._cached_system_prompt = None

        prompt = agent._build_system_prompt()

        assert "面试官偏好与岗位要求" in prompt
        assert "前端工程师" in prompt
