"""面试助手后端启动入口 — 组装依赖后通过 NiceGUI 启动。"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from nicegui import ui

from src.logging import setup_logging

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
from src.audio.manager import AudioManager
from src.audio.recorder import AudioRecorder
from src.config import get_settings
from src.framework.context import ContextConfig, ContextManager
from src.framework.prompt_builder import AgentConfig, PromptBuilder
from src.framework.skill import SkillLoader
from src.framework.tool_registry import ToolRegistry
from src.llm.client import OpenAICompatibleClient
from src.llm.config import LLMConfig
from src.storage.memory_module import MemoryModule
from src.storage.user_memory import UserMemoryStore
from src.tools import register_all
from src.tools._context import ctx as tool_ctx
from src.web.app import create_app
import src.web.ui as _web_ui  # noqa: F401 — registers @ui.page("/") at import time

LOGS_DIR = Path(__file__).parent.parent / "logs"
setup_logging(log_dir=LOGS_DIR, level=logging.INFO)
logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent / "skills"
USER_MEMORY_PATH = Path(__file__).parent.parent / "USER.md"
USER_MEMORY_CHAR_LIMIT = 3000

settings = get_settings()

_controller_ref: InterviewController | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _controller_ref
    logger.info("Interview Assistant starting up")
    os.makedirs(settings.RECORDINGS_DIR, exist_ok=True)
    os.makedirs(settings.CANDIDATES_DIR, exist_ok=True)
    os.makedirs("resumes", exist_ok=True)

    # ── Infrastructure ─────────────────────────────────────────────────────────
    memory_module = MemoryModule(candidates_dir=settings.CANDIDATES_DIR)

    user_memory_store = UserMemoryStore(USER_MEMORY_PATH, char_limit=USER_MEMORY_CHAR_LIMIT)
    user_memory_store.load()

    llm_config = LLMConfig(
        api_key=settings.QWEN_API_KEY,
        base_url=settings.QWEN_API_BASE_URL,
        model=settings.QWEN_MODEL,
        timeout_sec=settings.LLM_TIMEOUT_SEC,
        max_retries=settings.LLM_MAX_RETRIES,
    )
    llm_client = OpenAICompatibleClient(llm_config)

    # ── Skills & Tools ─────────────────────────────────────────────────────────
    skill_loader = SkillLoader(SKILLS_DIR)
    tool_registry = ToolRegistry()
    register_all(tool_registry)

    # ── Framework ─────────────────────────────────────────────────────────────
    ctx_config = ContextConfig(
        window_size=settings.CONTEXT_WINDOW_SIZE,
        token_budget=settings.CONTEXT_TOKEN_BUDGET,
        compression_round_threshold=settings.CONTEXT_COMPRESSION_THRESHOLD,
    )
    context_manager = ContextManager(ctx_config, llm_client)
    prompt_builder = PromptBuilder(
        skill_loader, tool_registry, memory_module, context_manager,
        user_memory_store=user_memory_store,
    )

    # ── Agents ────────────────────────────────────────────────────────────────
    resume_config = AgentConfig(
        name="resume",
        system_prompt=RESUME_AGENT_SYSTEM_PROMPT,
        skill_names=["resume_anchor"],
        tool_names=["parse_resume_pdf", "file_read", "file_write", "skill_view"],
    )
    interview_config = AgentConfig(
        name="interview",
        system_prompt=INTERVIEW_AGENT_SYSTEM_PROMPT,
        skill_names=["deep_dive", "dimension_switch", "behavioral_probe"],
        tool_names=["file_read"],
    )
    eval_config = AgentConfig(
        name="eval",
        system_prompt=EVAL_AGENT_SYSTEM_PROMPT,
        skill_names=[],
        tool_names=["skill_view"],
    )

    resume_agent = ResumeAgent(resume_config, prompt_builder, llm_client, tool_registry)
    interview_agent = InterviewAgent(
        interview_config, prompt_builder, llm_client, tool_registry, context_manager
    )
    eval_agent = EvalAgent(
        eval_config, prompt_builder, llm_client, tool_registry, memory_module,
        user_memory_store=user_memory_store,
    )

    # ── Audio ─────────────────────────────────────────────────────────────────
    if settings.MOCK_AUDIO:
        from src.audio.mock_manager import MockAudioManager
        audio_manager = MockAudioManager(
            script_path=settings.MOCK_AUDIO_SCRIPT,
            recordings_dir=str(settings.RECORDINGS_DIR),
        )
        logger.info("Audio: using MockAudioManager with script=%s", settings.MOCK_AUDIO_SCRIPT)
    else:
        import sys
        if sys.platform == "win32":
            from src.audio.wasapi import WasapiCapturer
            capturer = WasapiCapturer()
            if settings.STT_ENGINE == "xunfei":
                from src.audio.xunfei_stt import XunfeiRealtimeSTT
                candidate_stt = XunfeiRealtimeSTT(channel="candidate")
                interviewer_stt = XunfeiRealtimeSTT(channel="interviewer")
                logger.info("Audio: using XunfeiRealtimeSTT")
            else:
                from src.audio.baidu_stt import BaiduRealtimeSTT
                candidate_stt = BaiduRealtimeSTT(channel="candidate")
                interviewer_stt = BaiduRealtimeSTT(channel="interviewer")
                logger.info("Audio: using BaiduRealtimeSTT")
        else:
            from src.audio.mock import MockAudioCapturer, MockSTTEngine
            capturer = MockAudioCapturer()
            candidate_stt = MockSTTEngine()
            interviewer_stt = MockSTTEngine()
        recorder = AudioRecorder()
        audio_manager = AudioManager(
            capturer, candidate_stt, interviewer_stt, recorder,
            recordings_dir=str(settings.RECORDINGS_DIR),
        )

    # ── InterviewController (new) ─────────────────────────────────────────────
    controller = InterviewController(
        interview_agent, eval_agent, memory_module, audio_manager
    )
    _controller_ref = controller

    # ── MainAgent (single entry point for conversation) ──────────────────────
    main_agent = MainAgent(
        llm_client=llm_client,
        tool_registry=tool_registry,
        memory_module=memory_module,
        user_memory_store=user_memory_store,
    )
    main_agent.bind_resume_agent(resume_agent)
    main_agent.bind_controller(controller)

    # 注入工具依赖（在所有组件创建完毕后）
    tool_ctx.main_agent = main_agent
    tool_ctx.resume_agent = resume_agent
    tool_ctx.controller = controller
    tool_ctx.memory_module = memory_module
    tool_ctx.user_memory_store = user_memory_store
    tool_ctx.prompt_builder = prompt_builder
    tool_ctx.skill_loader = skill_loader

    # Inject dependencies into NiceGUI UI module and FastAPI app state
    _web_ui.set_dependencies(memory_module, llm_client, tool_registry, settings)
    app.state.controller = controller
    app.state.main_agent = main_agent
    app.state.memory_module = memory_module
    app.state.context_manager = context_manager
    app.state.settings = settings

    logger.info(
        "Interview Assistant ready on http://%s:%d", settings.HOST, settings.PORT
    )

    yield  # ── server running ──────────────────────────────────────────────

    logger.info("Interview Assistant shutting down")
    try:
        await controller.close_session()
    except Exception:
        logger.exception("Lifespan: close_session failed")


app = create_app(lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn

    ui.run_with(app, title="面试助手", language="zh-CN")
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
