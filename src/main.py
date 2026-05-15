"""面试助手后端启动入口 — 手动组装所有依赖后启动 uvicorn。"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import uvicorn

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
from src.tools.resume_parser import parse_resume_pdf
from src.tools.skill_tools import make_skill_tools
from src.web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent / "skills"


async def bootstrap() -> None:
    settings = get_settings()
    os.makedirs(settings.RECORDINGS_DIR, exist_ok=True)

    # ── Infrastructure ─────────────────────────────────────────────────────────
    db = Database(settings.DB_PATH)
    await db.initialize()
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
        tool_names=["parse_resume", "skills_list", "skill_view"],
    )
    interview_config = AgentConfig(
        name="interview",
        system_prompt=INTERVIEW_AGENT_SYSTEM_PROMPT,
        skill_names=["deep_dive", "dimension_switch", "behavioral_probe"],
        tool_names=[],
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
    # Use Mock implementations on non-Windows platforms (no WASAPI available)
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

    # ── Orchestrator & App ────────────────────────────────────────────────────
    orchestrator = Orchestrator(resume_agent, interview_agent, eval_agent, memory_module, audio_manager)
    app = create_app(orchestrator, memory_module, context_manager, settings)

    logger.info("Starting Interview Assistant on http://%s:%d", settings.HOST, settings.PORT)
    config = uvicorn.Config(
        app=app,
        host=settings.HOST,
        port=settings.PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()

    await db.close()


if __name__ == "__main__":
    asyncio.run(bootstrap())