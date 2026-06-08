"""dispatch_to_agent — 通用 Agent 分发工具，将任务委托给指定 Agent 执行。"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

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

    task = await _enrich_task_with_session_context(task)
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


async def _enrich_task_with_session_context(task: str) -> str:
    """注入当前 session 的文件路径，避免 ResumeAgent 猜测错误路径。"""
    session = await ctx.controller.get_session()
    if session is None:
        return task
    cid = session.candidate.id
    name = session.candidate.name or ""
    profile_md = f"candidates/{cid}/profile.md"
    resume_pdf = session.candidate.resume_pdf or ""
    stem = Path(resume_pdf).stem if resume_pdf else ""
    lines = [
        task,
        "",
        "[系统上下文 — 生成简报时读取持久化简历，解析任务仍用 resumes/ 路径]",
        f"- 候选人 ID: {cid}",
        f"- 姓名: {name}",
        f"- 持久化简历（file_read 首选）: {profile_md}",
        f"- 简报文件: candidates/{cid}/brief.md",
    ]
    if stem:
        lines.append(f"- PDF: resumes/{stem}.pdf")
        lines.append(f"- 临时 Markdown: resumes/{stem}.md")
    return "\n".join(lines)


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

        # 读取 ResumeAgent 写出的 Markdown 文件内容，读取成功后删除临时文件
        resume_markdown = ""
        markdown_path = result.get("markdown_path")
        if markdown_path:
            md_file = Path(markdown_path)
            try:
                resume_markdown = md_file.read_text(encoding="utf-8")
            except Exception:
                logger.warning("dispatch_to_agent: failed to read markdown file %s", markdown_path)
            else:
                try:
                    md_file.unlink()
                except OSError:
                    pass

        # 拒绝在简历 Markdown 为空时落盘 profile，避免覆盖既有数据为空文件
        if not resume_markdown.strip():
            logger.warning(
                "dispatch_to_agent: empty resume_markdown for candidate_id=%s, skip save_candidate",
                session.candidate.id,
            )
            result["warning"] = "简历正文为空，未写入候选人档案；请检查 PDF 解析结果"
        elif ctx.memory_module is not None:
            try:
                await ctx.memory_module.save_candidate(session.candidate, resume_markdown)
                session.candidate.resume_content = resume_markdown
            except Exception as exc:
                logger.exception("dispatch_to_agent: save_candidate failed")
                result["user_facing"] = f"候选人档案保存失败：{exc}。简历内容未持久化，请重试。"
                return
            if ctx.main_agent is not None:
                ctx.main_agent.set_candidate_context(session.candidate, interview_brief="")

    elif result_type == "brief_done":
        cid = session.candidate.id
        brief_text = str(result.get("brief", ""))

        if ctx.memory_module is not None:
            try:
                ctx.memory_module.save_brief(cid, brief_text)
            except Exception:
                logger.exception("dispatch_to_agent: save_brief failed")

        session.interview_brief = brief_text

        if ctx.main_agent is not None:
            ctx.main_agent.set_candidate_context(session.candidate, interview_brief=brief_text)
