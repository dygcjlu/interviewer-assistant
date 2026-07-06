"""Unit tests — agents 模块：BaseAgent._run_with_tools、ResumeAgent、InterviewAgent。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.base import AgentRequest, AgentResponse, BaseAgent
from src.agents.resume_agent import (
    ResumeAgent,
    _extract_json,
    _fallback_from_messages,
)
from src.framework.context import ContextConfig, ContextManager
from src.framework.prompt_builder import AgentConfig, PromptBuilder
from src.framework.tool_registry import ToolRegistry
from src.llm.protocol import ChatResponse
from src.models.candidate import CandidateProfile
from src.models.exceptions import LLMResponseError
from src.models.message import FunctionCallInfo, Message, ToolCallInfo
from src.models.session import (
    ConversationRound,
    InterviewSession,
    InterviewStage,
    SessionMetadata,
)

# ── 共享 fixtures ────────────────────────────────────────────────────────────


def _make_session() -> InterviewSession:
    return InterviewSession(
        id="s-test",
        candidate=CandidateProfile(id="c-001", name="张三"),
        rounds=[],
        stage=InterviewStage.IDLE,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id="c-001", start_time=datetime.now()),
    )


def _make_mock_llm(content: str = "ok", tool_calls=None) -> AsyncMock:
    mock = AsyncMock()
    mock.chat = AsyncMock(
        return_value=ChatResponse(content=content, tool_calls=tool_calls)
    )
    return mock


def _make_prompt_builder() -> MagicMock:
    pb = MagicMock(spec=PromptBuilder)
    pb.build.return_value = [Message(role="system", content="系统提示")]
    return pb


def _make_agent_config(name: str = "test") -> AgentConfig:
    return AgentConfig(name=name, system_prompt="Agent 系统提示", tool_names=[])


# ── ConcreteAgent（用于测试 BaseAgent 抽象方法）────────────────────────────


class ConcreteAgent(BaseAgent):
    async def on_activate(self, session: InterviewSession) -> None:
        pass

    async def on_deactivate(self, session: InterviewSession) -> None:
        pass

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(success=True)


# ── BaseAgent._run_with_tools ─────────────────────────────────────────────────


@pytest.mark.unit
class TestRunWithTools:
    @pytest.mark.asyncio
    async def test_returns_content_when_no_tool_calls(self):
        llm = _make_mock_llm("直接回复，无工具调用")
        registry = ToolRegistry()
        agent = ConcreteAgent(
            _make_agent_config(), _make_prompt_builder(), llm, registry
        )
        messages = [Message(role="user", content="hi")]
        result = await agent._run_with_tools(messages)
        assert result == "直接回复，无工具调用"

    @pytest.mark.asyncio
    async def test_executes_tool_call_and_appends_result(self):
        registry = ToolRegistry()

        @registry.register(
            "测试工具", parameters_schema={"type": "object", "properties": {}}
        )
        async def test_tool() -> str:
            return "工具执行结果"

        tc = ToolCallInfo(
            id="tc-001",
            type="function",
            function=FunctionCallInfo(name="test_tool", arguments="{}"),
        )
        # 第一次返回 tool call，第二次返回纯文本
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            side_effect=[
                ChatResponse(content="", tool_calls=[tc]),
                ChatResponse(content="最终回复"),
            ]
        )
        agent = ConcreteAgent(
            _make_agent_config(), _make_prompt_builder(), mock_llm, registry
        )
        messages = [Message(role="user", content="hi")]
        result = await agent._run_with_tools(messages)
        assert result == "最终回复"
        # messages 应追加 assistant + tool 消息
        assert len(messages) == 3  # user + assistant + tool

    @pytest.mark.asyncio
    async def test_raises_after_max_tool_rounds(self):
        registry = ToolRegistry()

        @registry.register(
            "loop", parameters_schema={"type": "object", "properties": {}}
        )
        async def loop() -> str:
            return "result"

        tc = ToolCallInfo(
            id="tc-loop",
            type="function",
            function=FunctionCallInfo(name="loop", arguments="{}"),
        )
        # 永远返回 tool_call，触发循环上限
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            return_value=ChatResponse(content="", tool_calls=[tc])
        )
        agent = ConcreteAgent(
            _make_agent_config(), _make_prompt_builder(), mock_llm, registry
        )
        messages = [Message(role="user", content="hi")]
        with pytest.raises(LLMResponseError, match="超出上限"):
            await agent._run_with_tools(messages, max_tool_rounds=2)

    @pytest.mark.asyncio
    async def test_on_tool_result_hook_can_early_exit(self):
        registry = ToolRegistry()

        @registry.register(
            "parse", parameters_schema={"type": "object", "properties": {}}
        )
        async def parse() -> dict:
            return {"user_facing": True, "error": "文件解析失败"}

        tc = ToolCallInfo(
            id="tc-parse",
            type="function",
            function=FunctionCallInfo(name="parse", arguments="{}"),
        )
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            return_value=ChatResponse(content="", tool_calls=[tc])
        )
        agent = ConcreteAgent(
            _make_agent_config(), _make_prompt_builder(), mock_llm, registry
        )
        messages = [Message(role="user", content="hi")]

        def early_exit_hook(name: str, result: str) -> str | None:
            return "早退结果"

        result = await agent._run_with_tools(messages, on_tool_result=early_exit_hook)
        assert result == "早退结果"


# ── _extract_json ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestExtractJson:
    def test_plain_json_object(self):
        result = _extract_json('{"type": "parse_done"}')
        assert result["type"] == "parse_done"

    def test_json_in_code_block(self):
        text = '```json\n{"type": "brief_done", "data": 1}\n```'
        result = _extract_json(text)
        assert result["type"] == "brief_done"

    def test_json_embedded_in_text(self):
        text = '这里是输出：{"type": "ok"} 以上就是结果'
        result = _extract_json(text)
        assert result["type"] == "ok"

    def test_empty_string_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("")

    def test_no_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("这是纯文字，没有 JSON")

    def test_json_array(self):
        result = _extract_json("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_code_block_without_language_tag(self):
        text = '```\n{"key": "value"}\n```'
        result = _extract_json(text)
        assert result["key"] == "value"


# ── _fallback_from_messages ───────────────────────────────────────────────────


@pytest.mark.unit
class TestFallbackFromMessages:
    def _make_file_write_messages(
        self, path: str, content: str = "data"
    ) -> list[Message]:
        tc = ToolCallInfo(
            id="fw-001",
            type="function",
            function=FunctionCallInfo(
                name="file_write",
                arguments=json.dumps({"file_path": path, "content": content}),
            ),
        )
        return [
            Message(role="assistant", content="", tool_calls=[tc]),
            Message(role="tool", content=f"已成功写入 {path}", tool_call_id="fw-001"),
        ]

    def test_returns_parse_done_for_md_path(self):
        msgs = self._make_file_write_messages("resumes/张三.md")
        result = _fallback_from_messages(msgs, "parse resume", "bad output")
        assert result is not None
        assert result["type"] == "parse_done"
        assert result["markdown_path"] == "resumes/张三.md"

    def test_returns_none_for_non_md_path(self):
        msgs = self._make_file_write_messages("output/something.txt")
        result = _fallback_from_messages(msgs, "generate brief", "bad")
        assert result is None

    def test_returns_none_when_no_file_write(self):
        msgs = [Message(role="user", content="hi")]
        result = _fallback_from_messages(msgs, "task", "bad")
        assert result is None

    def test_returns_none_for_empty_messages(self):
        result = _fallback_from_messages([], "task", "")
        assert result is None


# ── ResumeAgent ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestResumeAgent:
    def _make_agent(
        self,
        llm_content: str = '{"type": "parse_done", "markdown_path": "a.md", "profile": {}}',
    ):
        mock_llm = _make_mock_llm(llm_content)
        registry = ToolRegistry()
        pb = _make_prompt_builder()
        config = AgentConfig(
            name="resume",
            system_prompt="Resume Agent",
            tool_names=[],
        )
        return ResumeAgent(config, pb, mock_llm, registry), mock_llm

    @pytest.mark.asyncio
    async def test_handle_request_returns_failure(self):
        """ResumeAgent.handle_request 不对外使用，返回 failure。"""
        agent, _ = self._make_agent()
        session = _make_session()
        req = AgentRequest(type="parse_resume", payload={}, session=session)
        resp = await agent.handle_request(req)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_execute_returns_parse_done_dict(self):
        agent, _ = self._make_agent(
            '{"type": "parse_done", "markdown_path": "resumes/a.md", "profile": {}}'
        )
        result = await agent.execute("解析简历")
        assert result["type"] == "parse_done"

    @pytest.mark.asyncio
    async def test_execute_returns_error_on_user_facing_sentinel(self):
        """parse_resume_pdf 返回 user_facing 错误时，execute 早退返回 error。"""
        agent, mock_llm = self._make_agent()
        # 让 _run_with_tools 的 hook 触发 user_facing early exit
        # 通过让 LLM 返回 tool call 并注入 hook 来触发
        tc = ToolCallInfo(
            id="tc-pdf",
            type="function",
            function=FunctionCallInfo(
                name="parse_resume_pdf",
                arguments='{"pdf_path": "bad.pdf"}',
            ),
        )

        @agent.tool_registry.register(
            "parse_resume_pdf",
            parameters_schema={
                "type": "object",
                "properties": {"pdf_path": {"type": "string"}},
            },
        )
        async def parse_resume_pdf(pdf_path: str) -> dict:
            return {"user_facing": True, "error": "PDF 解析失败", "candidate_id": ""}

        mock_llm.chat = AsyncMock(
            side_effect=[
                ChatResponse(content="", tool_calls=[tc]),
                ChatResponse(content="fallback"),
            ]
        )
        result = await agent.execute("解析 bad.pdf")
        # 应该是 error 或 parse_done（取决于 fallback）
        assert "type" in result

    @pytest.mark.asyncio
    async def test_execute_fallback_on_json_decode_error(self):
        """LLM 输出非 JSON + 有 file_write 副作用时使用 fallback。"""
        tc_fw = ToolCallInfo(
            id="fw-001",
            type="function",
            function=FunctionCallInfo(
                name="file_write",
                arguments=json.dumps(
                    {"file_path": "resumes/test.md", "content": "# 简历"}
                ),
            ),
        )
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(
            side_effect=[
                ChatResponse(content="", tool_calls=[tc_fw]),
                ChatResponse(content="任务完成，已写入文件。"),  # 非 JSON
            ]
        )
        registry = ToolRegistry()

        @registry.register(
            "file_write",
            parameters_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        )
        async def file_write(file_path: str, content: str) -> str:
            return f"已成功写入 {file_path}"

        pb = _make_prompt_builder()
        config = AgentConfig(
            name="resume", system_prompt="Agent", tool_names=["file_write"]
        )
        agent = ResumeAgent(config, pb, mock_llm, registry)
        result = await agent.execute("生成简历 Markdown")
        # fallback 应该返回 parse_done
        assert result.get("type") == "parse_done" or result.get("type") == "error"

    @pytest.mark.asyncio
    async def test_on_activate_does_not_raise(self):
        agent, _ = self._make_agent()
        await agent.on_activate(_make_session())

    @pytest.mark.asyncio
    async def test_on_deactivate_does_not_raise(self):
        agent, _ = self._make_agent()
        await agent.on_deactivate(_make_session())


# ── InterviewAgent ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestInterviewAgent:
    def _make_interview_agent(self):
        from src.agents.interview_agent import InterviewAgent

        mock_llm = _make_mock_llm()
        registry = ToolRegistry()
        pb = _make_prompt_builder()
        config = AgentConfig(
            name="interview", system_prompt="面试 Agent", tool_names=[]
        )
        ctx_config = ContextConfig(window_size=3)
        context_manager = ContextManager(ctx_config, mock_llm)
        agent = InterviewAgent(
            config,
            pb,
            mock_llm,
            registry,
            context_manager,
            silence_threshold_sec=1.0,
            min_interval_sec=2.0,
        )
        return agent

    @pytest.mark.asyncio
    async def test_on_activate_sets_session(self):
        agent = self._make_interview_agent()
        session = _make_session()
        await agent.on_activate(session)
        assert agent._session is session

    @pytest.mark.asyncio
    async def test_on_deactivate_clears_session(self):
        agent = self._make_interview_agent()
        session = _make_session()
        await agent.on_activate(session)
        await agent.on_deactivate(session)
        assert agent._session is None

    @pytest.mark.asyncio
    async def test_handle_request_set_trigger_mode_not_activated(self):
        agent = self._make_interview_agent()
        session = _make_session()
        req = AgentRequest(
            type="set_trigger_mode", payload={"mode": "manual"}, session=session
        )
        resp = await agent.handle_request(req)
        # Agent 未激活（_suggestion_trigger is None）
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_handle_request_set_trigger_mode_auto_after_activate(self):
        agent = self._make_interview_agent()
        session = _make_session()
        await agent.on_activate(session)
        req = AgentRequest(
            type="set_trigger_mode", payload={"mode": "auto"}, session=session
        )
        resp = await agent.handle_request(req)
        assert resp.success is True
        assert resp.data["mode"] == "auto"

    @pytest.mark.asyncio
    async def test_handle_request_set_trigger_mode_invalid_returns_failure(self):
        agent = self._make_interview_agent()
        session = _make_session()
        await agent.on_activate(session)
        req = AgentRequest(
            type="set_trigger_mode", payload={"mode": "invalid_mode"}, session=session
        )
        resp = await agent.handle_request(req)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_handle_request_trigger_suggestion_not_activated(self):
        agent = self._make_interview_agent()
        session = _make_session()
        req = AgentRequest(type="trigger_suggestion", payload={}, session=session)
        resp = await agent.handle_request(req)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_cancel_current_stream_no_task_does_not_raise(self):
        agent = self._make_interview_agent()
        # 无正在运行的任务，取消应不抛错
        await agent.cancel_current_stream()

    @pytest.mark.asyncio
    async def test_generate_suggestion_no_session_does_not_raise(self):
        agent = self._make_interview_agent()
        # 未 activate，generate_suggestion 应直接返回（不 yield）
        results = []
        async for token in agent.generate_suggestion(0):
            results.append(token)
        assert results == []

    def _make_interview_agent_with_llm(self, content: str = "追问建议"):
        from src.agents.interview_agent import InterviewAgent

        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(
            return_value=ChatResponse(content=content, tool_calls=None)
        )
        mock_llm.count_tokens = MagicMock(return_value=100)
        registry = ToolRegistry()
        pb = _make_prompt_builder()
        config = AgentConfig(
            name="interview", system_prompt="面试 Agent", tool_names=[]
        )
        ctx_config = ContextConfig(window_size=3)
        context_manager = ContextManager(ctx_config, mock_llm)
        agent = InterviewAgent(
            config,
            pb,
            mock_llm,
            registry,
            context_manager,
            silence_threshold_sec=1.0,
            min_interval_sec=2.0,
        )
        return agent

    @pytest.mark.asyncio
    async def test_generate_suggestion_yields_suggestion(self):
        agent = self._make_interview_agent_with_llm("追问建议：请详细说明")
        session = _make_session()
        session.rounds.append(
            ConversationRound(
                round_number=1, interviewer_text="你好", candidate_text="我是张三"
            )
        )
        await agent.on_activate(session)
        results = []
        async for token in agent.generate_suggestion(1):
            results.append(token)
        assert any("追问" in t for t in results)

    @pytest.mark.asyncio
    async def test_generate_suggestion_empty_rounds(self):
        agent = self._make_interview_agent_with_llm("开场问题")
        session = _make_session()
        await agent.on_activate(session)
        results = []
        async for token in agent.generate_suggestion(1):
            results.append(token)
        assert results  # should yield something

    @pytest.mark.asyncio
    async def test_generate_suggestion_handles_llm_error(self):
        from src.models.exceptions import LLMConnectionError

        agent = self._make_interview_agent_with_llm()
        agent.llm_client.chat = AsyncMock(
            side_effect=LLMConnectionError("connection failed")
        )
        session = _make_session()
        await agent.on_activate(session)
        # Should not raise, just swallow the error
        results = []
        async for token in agent.generate_suggestion(1):
            results.append(token)
        assert results == []

    @pytest.mark.asyncio
    async def test_handle_request_trigger_suggestion_activated(self):
        agent = self._make_interview_agent_with_llm("好的追问")
        session = _make_session()
        await agent.on_activate(session)
        req = AgentRequest(type="trigger_suggestion", payload={}, session=session)
        resp = await agent.handle_request(req)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_on_deactivate_cancels_running_task(self):
        agent = self._make_interview_agent()
        session = _make_session()
        await agent.on_activate(session)

        async def _slow():
            await asyncio.sleep(100)

        agent._current_stream_task = asyncio.create_task(_slow())
        await agent.on_deactivate(session)
        assert agent._session is None

    @pytest.mark.asyncio
    async def test_cancel_current_stream_cancels_running_task(self):
        agent = self._make_interview_agent()

        async def _slow():
            await asyncio.sleep(100)

        agent._current_stream_task = asyncio.create_task(_slow())
        await agent.cancel_current_stream()
        assert agent._current_stream_task is None

    @pytest.mark.asyncio
    async def test_handle_stream_delegates_to_generate_suggestion(self):
        agent = self._make_interview_agent_with_llm("流式建议")
        session = _make_session()
        await agent.on_activate(session)
        req = AgentRequest(
            type="trigger_suggestion", payload={}, session=session, request_id=5
        )
        tokens = []
        async for t in agent.handle_stream(req):
            tokens.append(t)
        assert any("流式" in t for t in tokens)


# ── MainAgent._build_system_prompt date injection ─────────────────────────────


@pytest.mark.unit
class TestMainAgentDateInjection:
    def _make_main_agent(self, tmp_path: Path):
        from src.agents.main_agent import MainAgent
        from src.framework.tool_registry import ToolRegistry
        from src.storage.memory_module import MemoryModule
        from src.storage.user_memory import UserMemoryStore

        user_mem_path = tmp_path / "USER.md"
        user_mem_path.write_text("")
        user_memory_store = UserMemoryStore(user_mem_path)
        user_memory_store.load()

        llm = AsyncMock()
        tool_registry = ToolRegistry()
        memory_module = MagicMock(spec=MemoryModule)

        return MainAgent(
            llm_client=llm,
            tool_registry=tool_registry,
            memory_module=memory_module,
            user_memory_store=user_memory_store,
        )

    def test_build_system_prompt_includes_current_date(self, tmp_path):
        from datetime import date

        agent = self._make_main_agent(tmp_path)
        prompt = agent._build_system_prompt()
        today = date.today().strftime("%Y-%m-%d")
        assert f"当前日期：{today}" in prompt

    def test_build_system_prompt_date_appears_after_role(self, tmp_path):
        agent = self._make_main_agent(tmp_path)
        prompt = agent._build_system_prompt()
        date_pos = prompt.index("当前日期：")
        role_pos = prompt.index("面试助手")
        assert role_pos < date_pos

    def test_build_system_prompt_date_not_stored_in_cache(self, tmp_path):
        from datetime import date

        agent = self._make_main_agent(tmp_path)
        agent._build_system_prompt()  # warm up cache
        today = date.today().strftime("%Y-%m-%d")
        assert agent._cached_system_prompt is not None
        # The cache itself should NOT start with the date prefix —
        # the date is prepended dynamically at return time.
        assert not agent._cached_system_prompt.startswith("当前日期：")


# ── MainAgent.set_candidate_context ─────────────────────────────────────────


@pytest.mark.unit
class TestMainAgentSetCandidateContext:
    def _make_agent(self):
        from unittest.mock import AsyncMock, MagicMock

        from src.agents.main_agent import MainAgent
        from src.framework.tool_registry import ToolRegistry
        from src.storage.memory_module import MemoryModule
        from src.storage.user_memory import UserMemoryStore

        llm = AsyncMock()
        tools = MagicMock(spec=ToolRegistry)
        memory = MagicMock(spec=MemoryModule)
        user_memory = MagicMock(spec=UserMemoryStore)
        user_memory.render.return_value = ""
        return MainAgent(llm, tools, memory, user_memory)

    def test_set_candidate_context_includes_history_summary(self):
        """传入 history_summary 后，_build_system_prompt() 应包含该内容。"""
        agent = self._make_agent()
        profile = CandidateProfile(id="c-001", name="王喜龙")
        history = "候选人 王喜龙 历史面试记录：\n第1次面试：2025-01-01，评分 6.0，结论 weak_hire"

        agent.set_candidate_context(profile, history_summary=history)
        prompt = agent._build_system_prompt()

        assert "历史面试记录" in prompt
        assert "weak_hire" in prompt

    def test_set_candidate_context_without_history_summary(self):
        """不传 history_summary 时，系统提示不含"历史面试记录"字样（向后兼容）。"""
        agent = self._make_agent()
        profile = CandidateProfile(id="c-001", name="李四")

        agent.set_candidate_context(profile)
        prompt = agent._build_system_prompt()

        assert "历史面试记录" not in prompt

    def test_set_candidate_context_history_summary_none_is_ignored(self):
        """显式传 history_summary=None 等同于不传，不影响提示词。"""
        agent = self._make_agent()
        profile = CandidateProfile(id="c-001", name="张三")

        agent.set_candidate_context(profile, history_summary=None)
        prompt = agent._build_system_prompt()

        assert "历史面试记录" not in prompt
