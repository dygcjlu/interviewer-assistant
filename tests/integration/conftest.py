"""Integration 层共享 fixture：MockLLMClient + 测试 FastAPI 应用实例。"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from src.agents.eval_agent import EvalAgent
from src.agents.interview_agent import InterviewAgent
from src.agents.interview_controller import InterviewController
from src.agents.main_agent import MainAgent
from src.agents.prompts import (
    EVAL_AGENT_SYSTEM_PROMPT,
    INTERVIEW_AGENT_SYSTEM_PROMPT,
    RESUME_AGENT_SYSTEM_PROMPT,
)
from src.agents.resume_agent import ResumeAgent
from src.audio.mock_manager import MockAudioManager
from src.config import Settings
from src.framework.context import ContextConfig, ContextManager
from src.framework.prompt_builder import AgentConfig, PromptBuilder
from src.framework.skill import SkillLoader
from src.framework.tool_registry import ToolRegistry
from src.llm.protocol import ChatResponse, LLMClient, StreamChunk
from src.models.message import Message, ToolCallInfo
from src.storage.memory_module import MemoryModule
from src.storage.user_memory import UserMemoryStore
from src.tools import register_all
from src.tools._context import ctx as tool_ctx
from src.web.app import create_app


# ── MockLLMClient ──────────────────────────────────────────────────────────────


class MockLLMClient:
    """可编程 LLM Mock，支持预设响应队列。

    未配置时默认返回纯文字回复。
    """

    def __init__(self) -> None:
        self._chat_queue: deque[ChatResponse] = deque()
        self._stream_queue: deque[list[StreamChunk]] = deque()

    def push_chat(self, response: ChatResponse) -> None:
        """将下一次 chat() 返回的响应放入队列。"""
        self._chat_queue.append(response)

    def push_stream(self, chunks: list[StreamChunk]) -> None:
        """将下一次 chat_stream() 返回的 chunk 序列放入队列。"""
        self._stream_queue.append(chunks)

    async def chat(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        timeout_sec: float | None = None,
    ) -> ChatResponse:
        if self._chat_queue:
            return self._chat_queue.popleft()
        return ChatResponse(content="mock 回复", prompt_tokens=10, completion_tokens=5)

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        timeout_sec: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        if self._stream_queue:
            chunks = self._stream_queue.popleft()
            for chunk in chunks:
                yield chunk
            return

        # 默认：两个 delta + 一个 final
        yield StreamChunk(delta="mock ")
        yield StreamChunk(delta="回复")
        yield StreamChunk(
            delta="",
            is_final=True,
            accumulated_content="mock 回复",
            prompt_tokens=10,
            completion_tokens=5,
        )

    def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") // 3 for m in messages) + 1


# ── test_app fixture ───────────────────────────────────────────────────────────


def _build_test_app(tmp_path: Path, mock_llm: MockLLMClient):
    """构建使用临时目录和 MockLLM 的测试 FastAPI 应用。"""
    candidates_dir = tmp_path / "candidates"
    candidates_dir.mkdir()
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir()
    user_memory_path = tmp_path / "USER.md"
    user_memory_path.write_text("")

    test_settings = Settings(
        LLM_API_KEY="mock-key",
        CANDIDATES_DIR=candidates_dir,
        RECORDINGS_DIR=recordings_dir,
        CONTEXT_TOKEN_BUDGET=80000,
        MOCK_AUDIO=True,
        MOCK_AUDIO_SCRIPT="data/mock_script.json",
    )

    @asynccontextmanager
    async def test_lifespan(app):
        memory_module = MemoryModule(candidates_dir=candidates_dir)
        user_memory_store = UserMemoryStore(user_memory_path, char_limit=3000)
        user_memory_store.load()

        skill_loader = SkillLoader(Path("skills"))
        tool_registry = ToolRegistry()
        register_all(tool_registry)

        ctx_config = ContextConfig(
            window_size=6,
            token_budget=80000,
            compression_round_threshold=8,
        )
        context_manager = ContextManager(ctx_config, mock_llm)
        prompt_builder = PromptBuilder(
            skill_loader,
            tool_registry,
            memory_module,
            context_manager,
            user_memory_store=user_memory_store,
        )

        resume_config = AgentConfig(
            name="resume",
            system_prompt=RESUME_AGENT_SYSTEM_PROMPT,
            skill_names=["question_generation"],
            tool_names=["parse_resume_pdf", "file_read", "file_write", "skill_view"],
        )
        interview_config = AgentConfig(
            name="interview",
            system_prompt=INTERVIEW_AGENT_SYSTEM_PROMPT,
            full_history=True,
            include_suggestions=False,
        )
        eval_config = AgentConfig(
            name="eval",
            system_prompt=EVAL_AGENT_SYSTEM_PROMPT,
            skill_names=[],
            tool_names=[],
        )

        resume_agent = ResumeAgent(
            resume_config, prompt_builder, mock_llm, tool_registry
        )
        interview_agent = InterviewAgent(
            interview_config, prompt_builder, mock_llm, tool_registry, context_manager
        )
        eval_agent = EvalAgent(
            eval_config,
            prompt_builder,
            mock_llm,
            tool_registry,
            memory_module,
            user_memory_store=user_memory_store,
        )

        audio_manager = MockAudioManager(
            script_path="data/mock_script.json",
            recordings_dir=str(recordings_dir),
        )

        controller = InterviewController(
            interview_agent, eval_agent, memory_module, audio_manager
        )
        main_agent = MainAgent(
            llm_client=mock_llm,
            tool_registry=tool_registry,
            memory_module=memory_module,
            user_memory_store=user_memory_store,
        )

        # 注入全局工具上下文
        tool_ctx.main_agent = main_agent
        tool_ctx.resume_agent = resume_agent
        tool_ctx.controller = controller
        tool_ctx.memory_module = memory_module
        tool_ctx.user_memory_store = user_memory_store
        tool_ctx.prompt_builder = prompt_builder
        tool_ctx.skill_loader = skill_loader
        # 允许访问测试目录
        tool_ctx.allowed_read_dirs = [
            str(resumes_dir) + "/",
            str(candidates_dir) + "/",
            "resumes/",
            "candidates/",
        ]
        tool_ctx.allowed_write_dirs = [
            str(resumes_dir) + "/",
            str(candidates_dir) + "/",
            "resumes/",
            "candidates/",
        ]

        app.state.controller = controller
        app.state.main_agent = main_agent
        app.state.memory_module = memory_module
        app.state.context_manager = context_manager
        app.state.settings = test_settings
        app.state.startup_warnings = []

        yield

        # 清理全局工具上下文
        tool_ctx.main_agent = None
        tool_ctx.resume_agent = None
        tool_ctx.controller = None
        tool_ctx.memory_module = None
        tool_ctx.user_memory_store = None
        tool_ctx.prompt_builder = None
        tool_ctx.skill_loader = None

    return create_app(lifespan=test_lifespan)


@pytest.fixture
def mock_llm():
    """每个测试独立的 MockLLMClient 实例。"""
    return MockLLMClient()


@pytest_asyncio.fixture
async def client(tmp_path, mock_llm):
    """httpx AsyncClient，挂载测试 ASGI 应用（LifespanManager 触发 lifespan）。"""
    app = _build_test_app(tmp_path, mock_llm)
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac


@pytest_asyncio.fixture
async def interviewing_client(tmp_path, mock_llm):
    """已处于 interviewing 状态的 client（有 1 轮对话记录，便于测试 stop → evaluating）。"""
    from datetime import datetime

    from src.models.candidate import CandidateProfile
    from src.models.session import ConversationRound

    app = _build_test_app(tmp_path, mock_llm)
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            # 预置一名候选人到 memory
            memory: MemoryModule = app.state.memory_module
            candidate = CandidateProfile(id="cid-test-001", name="测试候选人")
            await memory.save_candidate(candidate, "# 测试简历\n\n技术背景：Python")

            # 创建会话并开始面试
            await app.state.controller.create_session("cid-test-001")
            await app.state.controller.start_interview()

            # 注入 1 轮对话记录，使 stop → evaluating（而非 completed）
            session = await app.state.controller.get_session()
            session.rounds.append(
                ConversationRound(
                    round_number=1,
                    interviewer_text="请介绍一下你的工作经历",
                    candidate_text="我有 3 年 Python 后端开发经验",
                    timestamp=datetime.now(),
                )
            )
            yield ac
