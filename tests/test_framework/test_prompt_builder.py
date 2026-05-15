"""Tests for PromptBuilder."""
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from src.framework.skill import SkillLoader
from src.framework.tool_registry import ToolRegistry
from src.framework.context import ContextManager, ContextConfig
from src.framework.prompt_builder import PromptBuilder, AgentConfig
from src.models.session import (
    InterviewSession, InterviewStage, SessionMetadata,
    ConversationRound, InterviewQuestion,
)
from src.models.candidate import CandidateProfile
from src.models.message import Message


SKILLS_DIR = Path(__file__).parents[2] / "skills"


def _make_session(with_summary: bool = False, with_rounds: bool = False) -> InterviewSession:
    candidate = CandidateProfile(
        id="c1",
        name="张三",
        resume_summary="5年后端经验，熟悉分布式系统",
        history_summary="历史摘要：上次面试评分 7.5" if with_summary else None,
    )
    session = InterviewSession(
        id="s1",
        candidate=candidate,
        question_plan=[
            InterviewQuestion(id=1, dimension="系统设计", question="设计缓存系统", follow_ups=["一致性怎么保证？"])
        ],
        rounds=[],
        stage=InterviewStage.INTERVIEWING,
        context_summary="",
        covered_dimensions={"算法"},
        working_notes="",
        metadata=SessionMetadata(candidate_id="c1", start_time=datetime.now()),
    )
    if with_rounds:
        session.rounds.append(ConversationRound(
            round_number=1,
            interviewer_text="介绍一下自己",
            candidate_text="我是一名后端工程师",
        ))
    return session


def _make_builder(with_rounds: bool = False) -> tuple[PromptBuilder, InterviewSession]:
    skill_loader = SkillLoader(SKILLS_DIR)
    registry = ToolRegistry()
    llm = MagicMock()
    ctx_manager = ContextManager(ContextConfig(window_size=6, compression_round_threshold=100), llm)
    memory = MagicMock()

    session = _make_session(with_rounds=with_rounds)

    builder = PromptBuilder(skill_loader, registry, memory, ctx_manager)
    return builder, session


def test_build_returns_list_of_messages() -> None:
    builder, session = _make_builder()
    config = AgentConfig(name="test", system_prompt="You are a test agent.", skill_names=[], tool_names=[])
    messages = builder.build(session, config)
    assert isinstance(messages, list)
    assert all(isinstance(m, Message) for m in messages)


def test_build_first_message_is_system_prompt() -> None:
    builder, session = _make_builder()
    config = AgentConfig(name="test", system_prompt="Agent identity here.", skill_names=[], tool_names=[])
    messages = builder.build(session, config)
    assert messages[0].role == "system"
    assert messages[0].content == "Agent identity here."


def test_build_includes_fixed_zone_with_candidate_name() -> None:
    builder, session = _make_builder()
    config = AgentConfig(name="test", system_prompt="X", skill_names=[], tool_names=[])
    messages = builder.build(session, config)
    combined = " ".join(m.content or "" for m in messages)
    assert "张三" in combined


def test_build_includes_skill_index_when_requested() -> None:
    builder, session = _make_builder()
    config = AgentConfig(name="test", system_prompt="X", skill_names=["deep_dive"], tool_names=[])
    messages = builder.build(session, config)
    combined = " ".join(m.content or "" for m in messages)
    assert "deep_dive" in combined


def test_build_no_skill_layer_when_no_skills() -> None:
    builder, session = _make_builder()
    config = AgentConfig(name="test", system_prompt="X", skill_names=[], tool_names=[])
    messages = builder.build(session, config)
    # Should not have a skill index layer (only system prompt + fixed zone)
    system_messages = [m for m in messages if m.role == "system"]
    # Layer 2 (skill) not present — all system messages should not contain skill names
    combined = " ".join(m.content or "" for m in system_messages)
    assert "可用面试技巧" not in combined


def test_build_includes_history_summary_when_present() -> None:
    skill_loader = SkillLoader(SKILLS_DIR)
    registry = ToolRegistry()
    llm = MagicMock()
    ctx_manager = ContextManager(ContextConfig(), llm)
    memory = MagicMock()
    builder = PromptBuilder(skill_loader, registry, memory, ctx_manager)

    session = _make_session(with_summary=True)
    config = AgentConfig(name="test", system_prompt="X", skill_names=[], tool_names=[])
    messages = builder.build(session, config)
    combined = " ".join(m.content or "" for m in messages)
    assert "历史摘要" in combined


def test_build_window_rounds_as_user_assistant() -> None:
    skill_loader = SkillLoader(SKILLS_DIR)
    registry = ToolRegistry()
    llm = MagicMock()
    config_ctx = ContextConfig(window_size=6, compression_round_threshold=100)
    ctx_manager = ContextManager(config_ctx, llm)
    memory = MagicMock()
    builder = PromptBuilder(skill_loader, registry, memory, ctx_manager)

    session = _make_session()
    # Manually inject a round into the context manager
    import asyncio
    round_ = ConversationRound(
        round_number=1,
        interviewer_text="自我介绍",
        candidate_text="我是后端工程师",
    )
    asyncio.get_event_loop().run_until_complete(ctx_manager.add_round(round_))

    config = AgentConfig(name="test", system_prompt="X", skill_names=[], tool_names=[])
    messages = builder.build(session, config)
    user_msgs = [m for m in messages if m.role == "user"]
    assert any("自我介绍" in (m.content or "") for m in user_msgs)


def test_agent_config_dataclass() -> None:
    config = AgentConfig(name="resume", system_prompt="prompt", skill_names=["deep_dive"], tool_names=["parse_resume"])
    assert config.name == "resume"
    assert "deep_dive" in config.skill_names
    assert "parse_resume" in config.tool_names