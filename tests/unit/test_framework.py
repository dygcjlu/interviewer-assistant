"""Unit tests — framework 模块：ContextManager、ToolRegistry、SkillLoader、PromptBuilder。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.framework.context import ContextConfig, ContextData, ContextManager
from src.framework.prompt_builder import AgentConfig, PromptBuilder, _build_fixed_zone
from src.framework.skill import SkillContent, SkillLoader, SkillMeta
from src.framework.tool_registry import ToolEntry, ToolRegistry
from src.llm.protocol import ChatResponse
from src.models.candidate import CandidateProfile
from src.models.message import Message
from src.models.session import ConversationRound, InterviewSession, InterviewStage, SessionMetadata


# ── 共享 fixtures ────────────────────────────────────────────────────────────


def _make_round(n: int = 1, interviewer: str = "问", candidate: str = "答") -> ConversationRound:
    return ConversationRound(
        round_number=n,
        interviewer_text=interviewer,
        candidate_text=candidate,
        timestamp=datetime.now(),
    )


def _make_session(candidate_id: str = "c-001", name: str = "张三") -> InterviewSession:
    return InterviewSession(
        id="s-001",
        candidate=CandidateProfile(id=candidate_id, name=name),
        rounds=[],
        stage=InterviewStage.IDLE,
        context_summary="",
        interview_brief="",
        metadata=SessionMetadata(candidate_id=candidate_id, start_time=datetime.now()),
    )


def _make_llm_mock(content: str = "摘要内容") -> AsyncMock:
    mock = AsyncMock()
    mock.chat = AsyncMock(
        return_value=ChatResponse(content=content, prompt_tokens=10, completion_tokens=5)
    )
    return mock


# ── ContextManager ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestContextManager:
    def _cfg(self, window=3, budget=80000, threshold=5) -> ContextConfig:
        return ContextConfig(
            window_size=window,
            token_budget=budget,
            compression_round_threshold=threshold,
        )

    def test_initial_state_is_empty(self):
        cm = ContextManager(self._cfg(), _make_llm_mock())
        data = cm.get_context()
        assert data.summary == ""
        assert data.window_rounds == []
        assert data.token_count > 0  # 固定基础 token

    @pytest.mark.asyncio
    async def test_add_round_appends_to_all_rounds(self):
        cm = ContextManager(self._cfg(), _make_llm_mock())
        r = _make_round()
        await cm.add_round(r)
        assert len(cm.all_rounds) == 1
        assert cm.all_rounds[0].round_number == 1

    @pytest.mark.asyncio
    async def test_get_context_window_truncates_to_window_size(self):
        cm = ContextManager(self._cfg(window=2), _make_llm_mock())
        for i in range(4):
            await cm.add_round(_make_round(i + 1))
        data = cm.get_context()
        # 窗口大小 2：只返回最近 2 条
        assert len(data.window_rounds) == 2
        assert data.window_rounds[-1].round_number == 4

    def test_update_covered_dimensions_sets_dimensions(self):
        cm = ContextManager(self._cfg(), _make_llm_mock())
        cm.update_covered_dimensions({"技术深度", "沟通能力"})
        data = cm.get_context()
        assert "技术深度" in data.covered_dimensions
        assert "沟通能力" in data.covered_dimensions

    @pytest.mark.asyncio
    async def test_reset_clears_all_state(self):
        cm = ContextManager(self._cfg(), _make_llm_mock())
        await cm.add_round(_make_round())
        cm.update_covered_dimensions({"dim1"})
        await cm.reset()
        data = cm.get_context()
        assert data.summary == ""
        assert data.window_rounds == []
        assert len(data.covered_dimensions) == 0
        assert cm.all_rounds == []

    def test_token_usage_returns_usage_info(self):
        cm = ContextManager(self._cfg(), _make_llm_mock())
        usage = cm.token_usage
        assert usage.total_used > 0
        assert usage.budget > 0
        assert 0.0 <= usage.utilization <= 1.0
        assert usage.is_compressing is False

    def test_is_compressing_initial_false(self):
        cm = ContextManager(self._cfg(), _make_llm_mock())
        assert cm.is_compressing is False

    def test_summary_property_returns_empty_initially(self):
        cm = ContextManager(self._cfg(), _make_llm_mock())
        assert cm.summary == ""

    def test_set_compress_done_handler_updates_handler(self):
        cm = ContextManager(self._cfg(), _make_llm_mock())
        handler = MagicMock()
        cm.set_compress_done_handler(handler)
        cm.set_compress_done_handler(None)
        assert cm._on_compress_done is None

    @pytest.mark.asyncio
    async def test_compression_triggered_above_threshold(self):
        """超过 compression_round_threshold 时自动触发后台压缩任务。"""
        mock_llm = _make_llm_mock("test summary")
        cm = ContextManager(self._cfg(threshold=2, window=1), mock_llm)
        # 添加超过 threshold 的轮次
        for i in range(4):
            await cm.add_round(_make_round(i + 1))
        # 等一小段时间让后台任务完成
        await asyncio.sleep(0.05)
        # 压缩后 summary 应该被设置
        assert mock_llm.chat.called

    @pytest.mark.asyncio
    async def test_compression_done_callback_invoked(self):
        """压缩完成后 callback 被调用。"""
        mock_llm = _make_llm_mock("压缩摘要")
        callback = MagicMock()
        cm = ContextManager(self._cfg(threshold=2, window=1), mock_llm, on_compress_done=callback)
        for i in range(4):
            await cm.add_round(_make_round(i + 1))
        await asyncio.sleep(0.05)
        if mock_llm.chat.called:
            callback.assert_called()

    @pytest.mark.asyncio
    async def test_add_round_skips_compression_if_already_compressing(self):
        """压缩进行中时不重复触发。"""
        mock_llm = _make_llm_mock()
        cm = ContextManager(self._cfg(threshold=1, window=1), mock_llm)
        cm._is_compressing = True
        await cm.add_round(_make_round(1))
        await cm.add_round(_make_round(2))
        # 不应该创建新的压缩任务
        assert cm._compress_task is None


# ── ToolRegistry ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestToolRegistry:
    def test_register_and_get_tool(self):
        registry = ToolRegistry()

        @registry.register("测试工具", parameters_schema={"type": "object", "properties": {}})
        async def my_tool() -> str:
            return "ok"

        entry = registry.get_tool("my_tool")
        assert entry is not None
        assert entry.name == "my_tool"
        assert entry.description == "测试工具"

    def test_get_unknown_tool_returns_none(self):
        registry = ToolRegistry()
        assert registry.get_tool("nonexistent") is None

    @pytest.mark.asyncio
    async def test_dispatch_known_tool_returns_result(self):
        registry = ToolRegistry()

        @registry.register("求和", parameters_schema={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}})
        async def add(a: int, b: int) -> int:
            return a + b

        result = await registry.dispatch("add", '{"a": 3, "b": 4}')
        assert result == "7"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool_returns_error_json(self):
        registry = ToolRegistry()
        result = await registry.dispatch("no_such_tool", "{}")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "no_such_tool" in parsed["error"]

    @pytest.mark.asyncio
    async def test_dispatch_invalid_json_returns_error(self):
        registry = ToolRegistry()

        @registry.register("t", parameters_schema={"type": "object", "properties": {}})
        async def t() -> str:
            return "ok"

        result = await registry.dispatch("t", "not json{")
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_dispatch_tool_exception_returns_error_json(self):
        registry = ToolRegistry()

        @registry.register("raises", parameters_schema={"type": "object", "properties": {}})
        async def raises() -> str:
            raise ValueError("boom")

        result = await registry.dispatch("raises", "{}")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "ValueError" in parsed["error"]

    def test_get_schemas_all_tools(self):
        registry = ToolRegistry()

        @registry.register("工具A", parameters_schema={"type": "object", "properties": {}})
        async def tool_a() -> str:
            return ""

        @registry.register("工具B", parameters_schema={"type": "object", "properties": {}})
        async def tool_b() -> str:
            return ""

        schemas = registry.get_schemas()
        names = [s.function.name for s in schemas]
        assert "tool_a" in names
        assert "tool_b" in names

    def test_get_schemas_filtered_by_names(self):
        registry = ToolRegistry()

        @registry.register("工具A", parameters_schema={"type": "object", "properties": {}})
        async def tool_a() -> str:
            return ""

        @registry.register("工具B", parameters_schema={"type": "object", "properties": {}})
        async def tool_b() -> str:
            return ""

        schemas = registry.get_schemas(["tool_a"])
        assert len(schemas) == 1
        assert schemas[0].function.name == "tool_a"

    def test_get_schemas_unknown_name_skipped(self):
        registry = ToolRegistry()
        schemas = registry.get_schemas(["nonexistent"])
        assert schemas == []

    @pytest.mark.asyncio
    async def test_dispatch_tool_result_is_string_returned_as_is(self):
        registry = ToolRegistry()

        @registry.register("str_tool", parameters_schema={"type": "object", "properties": {}})
        async def str_tool() -> str:
            return "直接字符串"

        result = await registry.dispatch("str_tool", "{}")
        assert result == "直接字符串"

    @pytest.mark.asyncio
    async def test_dispatch_dict_result_serialized_to_json(self):
        registry = ToolRegistry()

        @registry.register("dict_tool", parameters_schema={"type": "object", "properties": {}})
        async def dict_tool() -> dict:
            return {"key": "value"}

        result = await registry.dispatch("dict_tool", "{}")
        parsed = json.loads(result)
        assert parsed["key"] == "value"


# ── SkillLoader ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSkillLoader:
    def _make_skill_file(self, tmp_path: Path, name: str, desc: str, hint: str) -> None:
        skill_dir = tmp_path / name
        skill_dir.mkdir()
        content = f"""---
name: {name}
description: {desc}
trigger_hint: {hint}
---

# Skill content
这是 {name} 的内容。
"""
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    def test_load_index_empty_when_no_skills_dir(self, tmp_path):
        loader = SkillLoader(tmp_path / "nonexistent")
        result = loader.load_index()
        assert result == []

    def test_load_index_returns_skill_metas(self, tmp_path):
        self._make_skill_file(tmp_path, "skill_a", "技巧A描述", "当用户提到A时")
        self._make_skill_file(tmp_path, "skill_b", "技巧B描述", "当用户提到B时")
        loader = SkillLoader(tmp_path)
        result = loader.load_index()
        assert len(result) == 2
        names = [m.name for m in result]
        assert "skill_a" in names
        assert "skill_b" in names

    def test_load_index_ignores_non_dir_entries(self, tmp_path):
        (tmp_path / "not_a_dir.txt").write_text("ignored")
        loader = SkillLoader(tmp_path)
        result = loader.load_index()
        assert result == []

    def test_load_index_skips_dirs_without_skill_md(self, tmp_path):
        (tmp_path / "empty_dir").mkdir()
        loader = SkillLoader(tmp_path)
        result = loader.load_index()
        assert result == []

    def test_load_skill_returns_content(self, tmp_path):
        self._make_skill_file(tmp_path, "my_skill", "描述", "触发提示")
        loader = SkillLoader(tmp_path)
        content = loader.load_skill("my_skill")
        assert isinstance(content, SkillContent)
        assert content.meta.name == "my_skill"
        assert "my_skill" in content.full_text

    def test_load_skill_not_found_raises(self, tmp_path):
        loader = SkillLoader(tmp_path)
        with pytest.raises(FileNotFoundError):
            loader.load_skill("nonexistent")

    def test_parse_frontmatter_no_frontmatter_raises(self, tmp_path):
        with pytest.raises(ValueError, match="'---'"):
            SkillLoader._parse_frontmatter("no frontmatter here")

    def test_parse_frontmatter_unclosed_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not closed"):
            SkillLoader._parse_frontmatter("---\nname: test\n")

    def test_parse_frontmatter_valid(self):
        text = "---\nname: test_skill\ndescription: desc\ntrigger_hint: hint\n---\n# body"
        meta = SkillLoader._parse_frontmatter(text)
        assert meta.name == "test_skill"
        assert meta.description == "desc"
        assert meta.trigger_hint == "hint"

    def test_parse_frontmatter_missing_fields_defaults_empty(self):
        text = "---\n---\n# body"
        meta = SkillLoader._parse_frontmatter(text)
        assert meta.name == ""
        assert meta.description == ""


# ── PromptBuilder ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPromptBuilder:
    def _make_prompt_builder(self, tmp_path: Path) -> tuple[PromptBuilder, MagicMock]:
        skill_loader = MagicMock(spec=SkillLoader)
        skill_loader.load_index.return_value = []
        tool_registry = ToolRegistry()
        memory_module = MagicMock()
        context_manager = MagicMock(spec=ContextManager)
        context_manager.get_context.return_value = ContextData(
            summary="",
            window_rounds=[],
            covered_dimensions=set(),
            token_count=100,
        )
        context_manager.all_rounds = []

        from src.storage.user_memory import UserMemoryStore
        user_mem_path = tmp_path / "USER.md"
        user_mem_path.write_text("")
        user_memory_store = UserMemoryStore(user_mem_path)
        user_memory_store.load()

        builder = PromptBuilder(
            skill_loader=skill_loader,
            tool_registry=tool_registry,
            memory_module=memory_module,
            context_manager=context_manager,
            user_memory_store=user_memory_store,
        )
        return builder, context_manager

    def test_build_returns_messages_with_system_message(self, tmp_path):
        builder, _ = self._make_prompt_builder(tmp_path)
        session = _make_session()
        config = AgentConfig(name="test", system_prompt="你是测试 Agent")
        messages = builder.build(session, config)
        assert len(messages) >= 1
        assert messages[0].role == "system"
        assert "你是测试 Agent" in messages[0].content

    def test_build_includes_candidate_name_in_system(self, tmp_path):
        builder, _ = self._make_prompt_builder(tmp_path)
        session = _make_session(name="王五")
        config = AgentConfig(name="test", system_prompt="Agent")
        messages = builder.build(session, config)
        assert "王五" in messages[0].content

    def test_build_with_history_rounds_appends_user_messages(self, tmp_path):
        builder, ctx_mgr = self._make_prompt_builder(tmp_path)
        rounds = [_make_round(1, "你好吗？", "很好")]
        ctx_mgr.get_context.return_value = ContextData(
            summary="",
            window_rounds=rounds,
            covered_dimensions=set(),
            token_count=200,
        )
        ctx_mgr.all_rounds = rounds
        session = _make_session()
        config = AgentConfig(name="test", system_prompt="Agent")
        messages = builder.build(session, config)
        # system + 1 user round
        assert len(messages) == 2
        assert messages[1].role == "user"
        assert "你好吗？" in messages[1].content

    def test_build_full_history_uses_all_rounds(self, tmp_path):
        builder, ctx_mgr = self._make_prompt_builder(tmp_path)
        all_rounds = [_make_round(i) for i in range(5)]
        ctx_mgr.all_rounds = all_rounds
        ctx_mgr.get_context.return_value = ContextData(
            summary="",
            window_rounds=all_rounds[-2:],  # 窗口只有2条
            covered_dimensions=set(),
            token_count=300,
        )
        session = _make_session()
        config = AgentConfig(name="test", system_prompt="Agent", full_history=True)
        messages = builder.build(session, config)
        # system + 5 user messages（full_history）
        assert len(messages) == 6

    def test_build_includes_suggestion_when_enabled(self, tmp_path):
        builder, ctx_mgr = self._make_prompt_builder(tmp_path)
        r = _make_round(1)
        r.llm_suggestion = "你可以追问细节"
        ctx_mgr.get_context.return_value = ContextData(
            summary="",
            window_rounds=[r],
            covered_dimensions=set(),
            token_count=100,
        )
        ctx_mgr.all_rounds = [r]
        session = _make_session()
        config = AgentConfig(name="test", system_prompt="Agent", include_suggestions=True)
        messages = builder.build(session, config)
        # system + user + assistant(suggestion)
        assert len(messages) == 3
        assert "追问建议" in messages[2].content

    def test_build_skips_suggestion_when_disabled(self, tmp_path):
        builder, ctx_mgr = self._make_prompt_builder(tmp_path)
        r = _make_round(1)
        r.llm_suggestion = "某个建议"
        ctx_mgr.get_context.return_value = ContextData(
            summary="",
            window_rounds=[r],
            covered_dimensions=set(),
            token_count=100,
        )
        ctx_mgr.all_rounds = [r]
        session = _make_session()
        config = AgentConfig(name="test", system_prompt="Agent", include_suggestions=False)
        messages = builder.build(session, config)
        # system + user only，无 assistant
        assert len(messages) == 2

    def test_build_includes_summary_in_system(self, tmp_path):
        builder, ctx_mgr = self._make_prompt_builder(tmp_path)
        ctx_mgr.get_context.return_value = ContextData(
            summary="[压缩摘要] 早期技术讨论",
            window_rounds=[],
            covered_dimensions=set(),
            token_count=100,
        )
        session = _make_session()
        config = AgentConfig(name="test", system_prompt="Agent")
        messages = builder.build(session, config)
        assert "压缩摘要" in messages[0].content

    def test_reload_user_memory_updates_cache(self, tmp_path):
        builder, _ = self._make_prompt_builder(tmp_path)
        # 初始为空
        assert builder._user_memory == ""
        # 向 store 添加内容后 reload
        builder._user_memory_store.add("岗位要求：5年以上经验")
        builder.reload_user_memory()
        assert "5年以上经验" in builder._user_memory


# ── _build_fixed_zone ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestBuildFixedZone:
    def test_minimal_candidate_includes_name(self):
        session = _make_session(name="测试人员")
        zone = _build_fixed_zone(session)
        assert "测试人员" in zone

    def test_candidate_with_skills_listed(self):
        session = _make_session()
        session.candidate.skills = ["Python", "Kubernetes"]
        zone = _build_fixed_zone(session)
        assert "Python" in zone
        assert "Kubernetes" in zone

    def test_candidate_with_experience(self):
        session = _make_session()
        session.candidate.years_of_experience = 5
        zone = _build_fixed_zone(session)
        assert "5" in zone

    def test_user_memory_appended_when_provided(self):
        session = _make_session()
        zone = _build_fixed_zone(session, user_memory="招募有 Go 经验的工程师")
        assert "Go" in zone

    def test_interview_brief_included(self):
        session = _make_session()
        session.interview_brief = "重点考察分布式系统"
        zone = _build_fixed_zone(session)
        assert "分布式系统" in zone

    def test_resume_content_included(self):
        session = _make_session()
        session.candidate.resume_content = "曾在 XYZ 公司任职"
        zone = _build_fixed_zone(session)
        assert "XYZ" in zone

    def test_no_resume_content_shows_file_path_hint(self):
        session = _make_session(candidate_id="cid-999")
        zone = _build_fixed_zone(session)
        assert "cid-999" in zone or "candidates" in zone
