"""dispatch_to_agent — 通用 Agent 分发工具，将任务委托给指定 Agent 执行。"""
from __future__ import annotations

import json
import logging
import time

from ..logging import truncate
from ._context import ctx

logger = logging.getLogger(__name__)

DESCRIPTION = "将任务委托给指定 Agent（当前支持 resume Agent）执行，返回执行结果"
SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {
            "type": "string",
            "enum": ["resume"],
            "description": "目标 Agent，当前仅支持 resume",
        },
        "task": {
            "type": "string",
            "description": "任务描述（自然语言），例如：将 resumes/张三.pdf 解析为 Markdown 保存为 resumes/张三.md",
        },
    },
    "required": ["agent", "task"],
}


async def dispatch_to_agent(agent: str, task: str) -> str:
    if agent != "resume":
        return json.dumps({"type": "error", "message": f"不支持的 agent: {agent!r}"}, ensure_ascii=False)

    if ctx.resume_agent is None or ctx.controller is None:
        return json.dumps({"type": "error", "message": "服务未初始化"}, ensure_ascii=False)

    logger.info("dispatch_to_agent agent=%s task=%s", agent, truncate(task))
    start = time.perf_counter()

    try:
        result = await ctx.resume_agent.execute(task)
    except Exception as exc:
        logger.exception("dispatch_to_agent: resume_agent.execute raised")
        return json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False)

    result_type = result.get("type") if isinstance(result, dict) else None
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info("dispatch_to_agent done type=%s elapsed_ms=%.1f result=%s", result_type, elapsed_ms, truncate(json.dumps(result, ensure_ascii=False, default=str)))

    if result_type == "error":
        return json.dumps(result, ensure_ascii=False)

    # Apply side effects based on result type
    try:
        await _apply_side_effects(result_type, result)
    except Exception:
        logger.exception("dispatch_to_agent: side effects failed, result still returned")

    return json.dumps(result, ensure_ascii=False, default=str)


async def _apply_side_effects(result_type: str | None, result: dict) -> None:
    """根据结果类型执行副作用（更新 session、持久化）。"""
    from ..models.candidate import update_candidate_from_data

    session = await ctx.controller.get_session()
    if session is None:
        logger.warning("dispatch_to_agent: no active session, skipping side effects")
        return

    if result_type == "parse_done":
        profile_data = result.get("profile") or {}
        if profile_data:
            update_candidate_from_data(session.candidate, profile_data)
        markdown_path = result.get("markdown_path")
        if markdown_path:
            session.candidate.resume_markdown_path = markdown_path

        if ctx.memory_module is not None:
            try:
                await ctx.memory_module.save_candidate(session.candidate)
            except Exception:
                logger.exception("dispatch_to_agent: save_candidate failed")

    elif result_type == "questions_done":
        from ..models.session import InterviewQuestion
        questions = result.get("questions", [])
        if questions:
            session.question_plan = [
                InterviewQuestion(
                    id=i + 1,
                    dimension=q.get("dimension", "通用"),
                    question=q.get("question", ""),
                    follow_ups=q.get("follow_ups", []),
                    difficulty=q.get("difficulty", "medium"),
                )
                for i, q in enumerate(questions)
                if isinstance(q, dict)
            ]
        if ctx.main_agent is not None:
            ctx.main_agent.set_candidate_context(session.candidate, questions)

        if ctx.memory_module is not None:
            try:
                if session.question_plan:
                    await ctx.memory_module.save_interview(session)
            except Exception:
                logger.exception("dispatch_to_agent: save_interview failed")
