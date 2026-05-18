"""REST API 路由 — 所有业务逻辑委托给 Orchestrator / MemoryModule。"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from src.logging import bind_op, bind_session_id

from ..agents.base import AgentRequest, AgentResponse
from ..tools.resume_parser import parse_resume_pdf
from ..models.exceptions import SessionError
from .schemas import QuestionsUpdateRequest, StartInterviewRequest, SwitchAgentRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _orchestrator(request: Request):
    return request.app.state.orchestrator


def _memory(request: Request):
    return request.app.state.memory_module


def _to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def _session_err(exc: SessionError) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "session_error", "message": str(exc)})


# ── resume ────────────────────────────────────────────────────────────────────

@router.post("/resume/upload")
async def upload_resume(
    request: Request,
    file: UploadFile = File(...),
    candidate_id: str | None = None,
):
    bind_op("upload_resume")
    start = time.perf_counter()
    filename = file.filename or "resume.pdf"
    orch = _orchestrator(request)
    session = await orch.get_session()
    if session is None:
        session = await orch.create_session(candidate_id)
    bind_session_id(session.id)

    logger.info(
        "upload_resume start filename=%r candidate_id=%s",
        filename,
        candidate_id or session.candidate.id,
    )

    try:
        await orch.switch_agent("resume")
    except SessionError as exc:
        raise _session_err(exc)

    suffix = os.path.splitext(filename)[1].lower() or ".pdf"
    allowed_suffixes = {".pdf"}
    if suffix not in allowed_suffixes:
        logger.error("upload_resume invalid_file_type suffix=%r", suffix)
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_file_type", "message": f"仅支持 PDF 格式简历，收到的文件类型为 {suffix!r}"},
        )
    file_bytes = await file.read()
    logger.info("upload_resume file_read bytes=%d", len(file_bytes))

    resumes_dir = Path("resumes")
    resumes_dir.mkdir(exist_ok=True)
    timestamp = int(time.time())
    pdf_path = resumes_dir / f"{session.id}_{timestamp}.pdf"
    pdf_path.write_bytes(file_bytes)
    logger.info("upload_resume saved_pdf path=%s", pdf_path)

    # Pre-extract raw text so resume_text is available before/after agent parsing
    try:
        raw_extract = json.loads(await parse_resume_pdf(str(pdf_path)))
        if extracted := raw_extract.get("text", ""):
            session.candidate.resume_text = extracted
            logger.info("upload_resume pdf_text_extracted chars=%d", len(extracted))
    except Exception:
        logger.warning("upload_resume: pre-extract PDF text failed, continuing without it")

    parse_resp: AgentResponse = await orch.handle_request(
        AgentRequest(type="parse_resume", payload={"file_path": str(pdf_path)}, session=session)
    )

    if not parse_resp.success:
        logger.error("upload_resume parse_failed error=%s", parse_resp.error)
        raise HTTPException(status_code=500, detail={"code": "parse_error", "message": parse_resp.error})

    logger.info(
        "upload_resume parse_ok candidate_name=%r",
        session.candidate.name or "",
    )

    # Persist resume text as Markdown
    if session.candidate.resume_text:
        md_path = resumes_dir / f"{session.id}.md"
        cand_name = session.candidate.name or "候选人"
        md_content = f"# 简历 — {cand_name}\n\n{session.candidate.resume_text}"
        md_path.write_text(md_content, encoding="utf-8")
        session.candidate.resume_markdown_path = str(md_path.resolve())
        logger.info("upload_resume saved_markdown path=%s", md_path)

    memory = _memory(request)
    try:
        await memory.save_candidate(session.candidate)
    except Exception:
        logger.exception("upload_resume persist_candidate failed")

    q_resp: AgentResponse = await orch.handle_request(
        AgentRequest(type="generate_questions", payload={}, session=session)
    )

    if q_resp.success:
        from ..models.session import InterviewQuestion
        questions_data = q_resp.data.get("questions", [])
        session.question_plan = [
            InterviewQuestion(
                id=i + 1,
                dimension=q.get("dimension", "通用"),
                question=q.get("question", ""),
                follow_ups=q.get("follow_ups", []),
                difficulty=q.get("difficulty", "medium"),
            )
            for i, q in enumerate(questions_data)
            if isinstance(q, dict)
        ]
        logger.info(
            "upload_resume generate_questions_ok questions_count=%d",
            len(session.question_plan),
        )
    elif not q_resp.success:
        logger.warning("upload_resume generate_questions_failed error=%s", q_resp.error)

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "upload_resume done candidate_id=%s questions_count=%d elapsed_ms=%.1f",
        session.candidate.id,
        len(session.question_plan),
        elapsed_ms,
    )
    return {
        "candidate_id": session.candidate.id,
        "profile": _to_dict(session.candidate),
        "questions": _to_dict(session.question_plan),
    }


@router.get("/resume/profile")
async def get_profile(request: Request, candidate_id: str = Query(...)):
    memory = _memory(request)
    candidate = await memory.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "候选人不存在"})
    session = await _orchestrator(request).get_session()
    questions: list[Any] = (
        _to_dict(session.question_plan)
        if session and session.candidate.id == candidate_id and session.question_plan
        else []
    )
    if not questions:
        questions = await memory.get_latest_question_plan(candidate_id)
    return {"candidate_id": candidate_id, "profile": _to_dict(candidate), "questions": questions}


# ── questions ─────────────────────────────────────────────────────────────────

@router.get("/interview/questions")
async def get_questions(request: Request, candidate_id: str = Query(...)):
    session = await _orchestrator(request).get_session()
    if session is None or session.candidate.id != candidate_id:
        raise HTTPException(status_code=404, detail={"code": "no_session", "message": "无对应会话"})
    return {"questions": _to_dict(session.question_plan)}


@router.put("/interview/questions")
async def update_questions(request: Request, body: QuestionsUpdateRequest):
    session = await _orchestrator(request).get_session()
    if session is None or session.candidate.id != body.candidate_id:
        raise HTTPException(status_code=404, detail={"code": "no_session", "message": "无对应会话"})
    from ..models.session import InterviewQuestion
    session.question_plan = [
        InterviewQuestion(
            id=q.get("id", i + 1),
            dimension=q.get("dimension", "通用"),
            question=q.get("question", ""),
            follow_ups=q.get("follow_ups", []),
            difficulty=q.get("difficulty", "medium"),
        )
        for i, q in enumerate(body.questions)
    ]
    return {"questions": _to_dict(session.question_plan)}


# ── interview lifecycle ───────────────────────────────────────────────────────

@router.post("/interview/start")
async def start_interview(request: Request, body: StartInterviewRequest):
    bind_op("start_interview")
    orch = _orchestrator(request)
    session = await orch.get_session()
    if session is None:
        session = await orch.create_session(body.candidate_id)

    try:
        await orch.switch_agent("interview")
    except SessionError as exc:
        raise _session_err(exc)

    if body.trigger_mode != "auto":
        await orch.handle_request(
            AgentRequest(
                type="set_trigger_mode",
                payload={"mode": body.trigger_mode},
                session=session,
            )
        )
    session.metadata.trigger_mode = body.trigger_mode
    bind_session_id(session.id)
    logger.info(
        "start_interview done session_id=%s trigger_mode=%s stage=%s",
        session.id,
        body.trigger_mode,
        session.stage.value,
    )
    return {"session_id": session.id, "stage": session.stage.value}


@router.post("/interview/stop")
async def stop_interview(request: Request):
    bind_op("stop_interview")
    orch = _orchestrator(request)
    session = await orch.get_session()
    if session is None:
        raise HTTPException(status_code=409, detail={"code": "no_session", "message": "无活跃会话"})

    try:
        await orch.switch_agent("eval")
    except SessionError as exc:
        raise _session_err(exc)

    bind_session_id(session.id)
    logger.info(
        "stop_interview done session_id=%s total_rounds=%d stage=%s",
        session.id,
        len(session.rounds),
        session.stage.value,
    )
    return {
        "session_id": session.id,
        "stage": session.stage.value,
        "total_rounds": len(session.rounds),
    }


# ── session switch ────────────────────────────────────────────────────────────

@router.post("/session/switch")
async def switch_agent(request: Request, body: SwitchAgentRequest):
    orch = _orchestrator(request)
    try:
        await orch.switch_agent(body.target_agent)
    except SessionError as exc:
        raise _session_err(exc)
    return {"stage": orch.stage.value, "active_agent": orch.active_agent_name}


# ── suggestion ────────────────────────────────────────────────────────────────

@router.post("/interview/suggest")
async def trigger_suggest(request: Request):
    orch = _orchestrator(request)
    session = await orch.get_session()
    if session is None:
        raise HTTPException(status_code=409, detail={"code": "no_session", "message": "无活跃会话"})
    resp = await orch.handle_request(
        AgentRequest(type="trigger_suggestion", payload={}, session=session)
    )
    if not resp.success:
        raise HTTPException(status_code=409, detail={"code": "trigger_error", "message": resp.error})
    return resp.data


# ── eval ─────────────────────────────────────────────────────────────────────

@router.get("/interview/eval")
async def get_eval(request: Request, interview_id: str | None = None):
    orch = _orchestrator(request)
    memory = _memory(request)

    if interview_id:
        report = await memory.get_eval_report(interview_id)
        if report is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "评价报告不存在"})
        return {"report": _to_dict(report)}

    session = await orch.get_session()
    if session is None:
        raise HTTPException(status_code=409, detail={"code": "no_session", "message": "无活跃会话"})

    # Save interview record first so EvalReport FK constraint is satisfied
    try:
        await memory.save_interview(session)
    except Exception:
        logger.exception("get_eval: pre-save interview failed")

    resp = await orch.handle_request(
        AgentRequest(type="generate_eval", payload={}, session=session)
    )
    if not resp.success:
        raise HTTPException(status_code=500, detail={"code": "eval_error", "message": resp.error})
    try:
        await orch.close_session()
    except Exception:
        logger.exception("get_eval: close_session failed")
    return {"report": _to_dict(resp.data["report"])}


# ── candidates ────────────────────────────────────────────────────────────────

@router.get("/candidates")
async def list_candidates(
    request: Request,
    keyword: str = "",
    limit: int = 20,
    offset: int = 0,
):
    memory = _memory(request)
    candidates = await memory.search_candidates(keyword=keyword, limit=limit + offset)
    paged = candidates[offset : offset + limit]
    return {
        "candidates": [_to_dict(c) for c in paged],
        "total": len(candidates),
    }


@router.get("/candidates/{candidate_id}/history")
async def get_candidate_history(request: Request, candidate_id: str):
    memory = _memory(request)
    candidate = await memory.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "候选人不存在"})
    history = await memory.get_candidate_history(candidate_id)
    return {
        "candidate": _to_dict(candidate),
        "interviews": _to_dict(history.past_interviews) if history else [],
    }


# ── recordings ────────────────────────────────────────────────────────────────

@router.get("/recordings/{session_id}/rounds/{round_number}")
async def get_round_recording(
    request: Request,
    session_id: str,
    round_number: int,
    source: str | None = None,
):
    settings = request.app.state.settings
    base = settings.RECORDINGS_DIR
    rounds_dir = os.path.join(base, session_id, "rounds")
    candidates = [
        os.path.join(rounds_dir, f"round_{round_number:03d}_{source}.wav") if source else None,
        os.path.join(rounds_dir, f"round_{round_number:03d}_candidate.wav"),
        os.path.join(rounds_dir, f"round_{round_number:03d}_interviewer.wav"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return FileResponse(path, media_type="audio/wav")
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "录音文件不存在"})


# ── session state ─────────────────────────────────────────────────────────────

@router.get("/session/current")
async def get_current_session(request: Request):
    orch = _orchestrator(request)
    session = await orch.get_session()
    if session is None:
        return {"session": None}
    cm = getattr(request.app.state, "context_manager", None)
    ctx = cm.get_context() if cm is not None else None
    return {
        "session": {
            "id": session.id,
            "stage": session.stage.value,
            "active_agent": orch.active_agent_name,
            "candidate_name": session.candidate.name,
            "trigger_mode": session.metadata.trigger_mode,
            "rounds_count": len(session.rounds),
            "token_used": ctx.token_count if ctx else 0,
            "token_budget": (
                request.app.state.settings.CONTEXT_TOKEN_BUDGET
                if request.app.state.settings
                else 80000
            ),
        }
    }