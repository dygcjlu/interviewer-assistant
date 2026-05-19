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
from src.agents.orchestrator import Orchestrator
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
from src.storage.database import Database
from src.storage.memory_module import MemoryModule
from src.tools.resume_parser import parse_resume_pdf, read_resume_markdown
from src.tools.skill_tools import make_skill_tools
from src.web.app import create_app
import src.web.ui as _web_ui  # noqa: F401 — registers @ui.page("/") at import time

LOGS_DIR = Path(__file__).parent.parent / "logs"
setup_logging(log_dir=LOGS_DIR, level=logging.INFO)
logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent / "skills"

settings = get_settings()

# Populated during lifespan startup — used for clean shutdown
_db: Database | None = None
_orchestrator_ref: Orchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _db, _orchestrator_ref
    logger.info("Interview Assistant starting up")
    os.makedirs(settings.RECORDINGS_DIR, exist_ok=True)
    os.makedirs("resumes", exist_ok=True)

    # ── Infrastructure ─────────────────────────────────────────────────────────
    db = Database(settings.DB_PATH)
    await db.initialize()
    _db = db
    memory_module = MemoryModule(db)

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

    @tool_registry.register(description="解析候选人简历 PDF 文件，提取文本内容")
    async def parse_resume(file_path: str) -> str:
        return await parse_resume_pdf(file_path)

    tool_registry.register(description="读取候选人简历 Markdown 文件的完整内容")(read_resume_markdown)

    skills_list_fn, skill_view_fn = make_skill_tools(skill_loader)
    tool_registry.register(description="列出可用面试技巧索引")(skills_list_fn)
    tool_registry.register(description="查看指定面试技巧的完整内容")(skill_view_fn)

    # ── Framework ─────────────────────────────────────────────────────────────
    ctx_config = ContextConfig(
        window_size=settings.CONTEXT_WINDOW_SIZE,
        token_budget=settings.CONTEXT_TOKEN_BUDGET,
        compression_round_threshold=settings.CONTEXT_COMPRESSION_THRESHOLD,
    )
    context_manager = ContextManager(ctx_config, llm_client)
    prompt_builder = PromptBuilder(skill_loader, tool_registry, memory_module, context_manager)

    # ── Agents ────────────────────────────────────────────────────────────────
    resume_config = AgentConfig(
        name="resume",
        system_prompt=RESUME_AGENT_SYSTEM_PROMPT,
        skill_names=["resume_anchor"],
        tool_names=["parse_resume", "read_resume_markdown", "skills_list", "skill_view"],
    )
    interview_config = AgentConfig(
        name="interview",
        system_prompt=INTERVIEW_AGENT_SYSTEM_PROMPT,
        skill_names=["deep_dive", "dimension_switch", "behavioral_probe"],
        tool_names=["read_resume_markdown"],
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
    eval_agent = EvalAgent(eval_config, prompt_builder, llm_client, tool_registry, memory_module)

    # ── Audio ─────────────────────────────────────────────────────────────────
    import sys
    if sys.platform == "win32":
        from src.audio.wasapi import WasapiCapturer
        from src.audio.baidu_stt import BaiduRealtimeSTT
        capturer = WasapiCapturer()
        candidate_stt = BaiduRealtimeSTT(channel="candidate")
        interviewer_stt = BaiduRealtimeSTT(channel="interviewer")
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

    # ── Orchestrator ──────────────────────────────────────────────────────────
    orchestrator = Orchestrator(
        resume_agent, interview_agent, eval_agent, memory_module, audio_manager
    )
    _orchestrator_ref = orchestrator

    # Inject dependencies into NiceGUI UI module and FastAPI app state
    _web_ui.set_dependencies(orchestrator, memory_module, llm_client, tool_registry, settings)
    app.state.orchestrator = orchestrator
    app.state.memory_module = memory_module
    app.state.context_manager = context_manager
    app.state.settings = settings

    logger.info(
        "Interview Assistant ready on http://%s:%d", settings.HOST, settings.PORT
    )

    yield  # ── server running ──────────────────────────────────────────────

    logger.info("Interview Assistant shutting down")
    try:
        await orchestrator.close_session()
    except Exception:
        logger.exception("Lifespan: close_session failed")
    if _db is not None:
        await _db.close()


app = create_app(lifespan=lifespan)


if __name__ == "__main__":
    import uvicorn

    # Mount NiceGUI routes onto the FastAPI app (non-blocking, wraps our lifespan)
    ui.run_with(app, title="面试助手", language="zh-CN")
    logger.info("Starting on http://%s:%d", settings.HOST, settings.PORT)
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
