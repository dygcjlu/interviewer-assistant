"""REST API 路由 — 业务逻辑委托给 MainAgent / InterviewController / MemoryModule。"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from src.logging import bind_op, bind_session_id

from ..agents.base import AgentRequest, AgentResponse
from ..models.exceptions import SessionError
from .schemas import (
    ChatRequest,
    CandidateSelectRequest,
    QuestionsUpdateRequest,
    StartInterviewRequest,
    SwitchAgentRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _controller(request: Request):
    return request.app.state.controller


def _main_agent(request: Request):
    return request.app.state.main_agent


def _memory(request: Request):
    return request.app.state.memory_module


def _to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def _session_err(exc: SessionError) -> HTTPException:
    logger.warning("session_error: %s", exc)
    return HTTPException(status_code=409, detail={"code": "session_error", "message": str(exc)})


# ── chat (MainAgent) ──────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(request: Request, body: ChatRequest):
    """接收用户消息，流式转发到 MainAgent。"""
    bind_op("chat")
    main_agent = _main_agent(request)
    if main_agent is None:
        raise HTTPException(status_code=503, detail={"code": "not_ready", "message": "MainAgent 未初始化"})

    async def _stream():
        async for chunk in main_agent.handle_chat(body.message):
            yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── candidate select ──────────────────────────────────────────────────────────

@router.post("/candidate/select")
async def select_candidate(request: Request, body: CandidateSelectRequest):
    """选中候选人，更新 MainAgent 上下文。"""
    bind_op("candidate_select")
    memory = _memory(request)
    main_agent = _main_agent(request)
    controller = _controller(request)

    candidate = await memory.get_candidate(body.candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "候选人不存在"})

    # Ensure session exists with this candidate
    if controller is not None:
        session = await controller.get_session()
        if session is None or session.candidate.id != body.candidate_id:
            session = await controller.create_session(body.candidate_id)

    # Load question plan
    questions: list[dict] = []
    if controller is not None:
        session = await controller.get_session()
        if session and session.question_plan:
            questions = [
                {
                    "id": q.id,
                    "dimension": q.dimension,
                    "question": q.question,
                    "follow_ups": list(q.follow_ups),
                    "difficulty": q.difficulty,
                }
                for q in session.question_plan
            ]
    if not questions:
        questions = await memory.get_latest_question_plan(body.candidate_id)

    # Update MainAgent context
    if main_agent is not None:
        main_agent.set_candidate_context(candidate, questions)

    resume_markdown = await memory.get_resume_markdown(body.candidate_id)

    latest_report = await memory.get_latest_eval_report(body.candidate_id)
    eval_report = _to_dict(latest_report) if latest_report is not None else None

    logger.info("candidate_select done candidate_id=%s name=%s", body.candidate_id, candidate.name)
    return {
        "candidate_id": body.candidate_id,
        "profile": _to_dict(candidate),
        "questions": questions,
        "resume_markdown": resume_markdown,
        "eval_report": eval_report,
    }


# ── resume ────────────────────────────────────────────────────────────────────

def _safe_stem(filename: str) -> str:
    """将文件名转换为安全的 stem（保留汉字、字母、数字、-、_）。"""
    stem = Path(filename).stem
    safe = re.sub(r"[^\w\u4e00-\u9fff.\-]", "_", stem).strip("_")
    return safe or "resume"


@router.post("/resume/upload")
async def upload_resume(
    request: Request,
    file: UploadFile = File(...),
    candidate_id: str | None = None,
    overwrite: bool = False,
):
    """上传 PDF 简历：仅保存文件，返回 file_path 和 safe_stem。解析由前端确认后触发。"""
    bind_op("upload_resume")
    start = time.perf_counter()
    filename = file.filename or "resume.pdf"
    controller = _controller(request)
    memory = _memory(request)

    suffix = os.path.splitext(filename)[1].lower() or ".pdf"
    if suffix not in {".pdf"}:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_file_type", "message": f"仅支持 PDF 格式简历，收到的文件类型为 {suffix!r}"},
        )

    safe_stem = _safe_stem(filename)

    # 去重检查
    if not candidate_id and not overwrite:
        existing = await memory.get_candidate_by_name(safe_stem)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "duplicate_candidate",
                    "message": f"候选人「{safe_stem}」已存在，请确认是否覆盖",
                    "existing_candidate_id": existing.id,
                    "existing_candidate_name": existing.name,
                },
            )

    # Ensure session exists
    session = await controller.get_session() if controller else None
    if session is None and controller is not None:
        session = await controller.create_session(candidate_id)
    elif session is not None and candidate_id and session.candidate.id != candidate_id:
        session = await controller.create_session(candidate_id)
    elif session is not None and not candidate_id:
        session = await controller.create_session(None)

    if session:
        bind_session_id(session.id)

    logger.info(
        "upload_resume start filename=%r safe_stem=%r candidate_id=%s overwrite=%s",
        filename,
        safe_stem,
        candidate_id or (session.candidate.id if session else ""),
        overwrite,
    )

    file_bytes = await file.read()

    resumes_dir = Path("resumes")
    resumes_dir.mkdir(exist_ok=True)
    pdf_path = resumes_dir / f"{safe_stem}.pdf"
    pdf_path.write_bytes(file_bytes)

    if session:
        session.candidate.resume_pdf = str(pdf_path)

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info("upload_resume saved file_path=%s elapsed_ms=%.1f", pdf_path, elapsed_ms)

    return {
        "file_path": str(pdf_path),
        "safe_stem": safe_stem,
        "session_id": session.id if session else str(uuid.uuid4()),
        "candidate_id": session.candidate.id if session else None,
    }


@router.get("/resume/profile")
async def get_profile(request: Request, candidate_id: str = Query(...)):
    memory = _memory(request)
    candidate = await memory.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "候选人不存在"})
    controller = _controller(request)
    session = None
    if controller:
        s = await controller.get_session()
        if s and s.candidate.id == candidate_id:
            session = s
    questions: list[Any] = (
        _to_dict(session.question_plan) if session and session.question_plan else []
    )
    if not questions:
        questions = await memory.get_latest_question_plan(candidate_id)
    resume_markdown = await memory.get_resume_markdown(candidate_id)
    return {
        "candidate_id": candidate_id,
        "profile": _to_dict(candidate),
        "questions": questions,
        "resume_markdown": resume_markdown,
    }


# ── questions ─────────────────────────────────────────────────────────────────

@router.get("/interview/questions")
async def get_questions(request: Request, candidate_id: str = Query(...)):
    controller = _controller(request)
    session = await controller.get_session()
    if session is None or session.candidate.id != candidate_id:
        raise HTTPException(status_code=404, detail={"code": "no_session", "message": "无对应会话"})
    return {"questions": _to_dict(session.question_plan)}


@router.put("/interview/questions")
async def update_questions(request: Request, body: QuestionsUpdateRequest):
    controller = _controller(request)
    session = await controller.get_session()
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
    controller = _controller(request)
    if controller is None:
        raise HTTPException(status_code=503, detail={"code": "not_ready", "message": "服务未初始化"})

    session = await controller.get_session()
    if session is None:
        session = await controller.create_session(body.candidate_id)
    try:
        await controller.start_interview()
    except SessionError as exc:
        raise _session_err(exc)

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
    controller = _controller(request)
    if controller is None:
        raise HTTPException(status_code=503, detail={"code": "not_ready", "message": "服务未初始化"})

    session = await controller.get_session()
    if session is None:
        raise HTTPException(status_code=409, detail={"code": "no_session", "message": "无活跃会话"})
    try:
        await controller.stop_interview()
    except SessionError as exc:
        raise _session_err(exc)
    bind_session_id(session.id)
    total_rounds = len(session.rounds)
    logger.info("stop_interview done session_id=%s total_rounds=%d", session.id, total_rounds)
    return {
        "session_id": session.id,
        "stage": session.stage.value,
        "total_rounds": total_rounds,
    }


# ── session switch (legacy — maps to controller operations) ───────────────────

@router.post("/session/switch")
async def switch_agent(request: Request, body: SwitchAgentRequest):
    controller = _controller(request)
    if controller is None:
        raise HTTPException(status_code=503, detail={"code": "not_ready", "message": "服务未初始化"})
    try:
        if body.target_agent == "interview":
            await controller.start_interview()
        elif body.target_agent == "eval":
            await controller.stop_interview()
        else:
            raise SessionError(f"不支持的目标 Agent: {body.target_agent!r}")
    except SessionError as exc:
        raise _session_err(exc)
    return {"stage": controller.stage.value, "active_agent": "main"}


# ── suggestion ────────────────────────────────────────────────────────────────

@router.post("/interview/suggest")
async def trigger_suggest(request: Request):
    controller = _controller(request)
    session = await controller.get_session() if controller else None
    if session is None:
        raise HTTPException(status_code=409, detail={"code": "no_session", "message": "无活跃会话"})
    resp = await controller.interview_agent.handle_request(
        AgentRequest(type="trigger_suggestion", payload={}, session=session)
    )
    if not resp.success:
        raise HTTPException(status_code=409, detail={"code": "trigger_error", "message": resp.error})
    return resp.data


# ── eval ─────────────────────────────────────────────────────────────────────

@router.get("/interview/eval")
async def get_eval(request: Request, interview_id: str | None = None):
    controller = _controller(request)
    memory = _memory(request)

    if interview_id:
        report = await memory.get_eval_report(interview_id)
        if report is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "评价报告不存在"})
        return {"report": _to_dict(report)}

    session = await controller.get_session() if controller else None
    if session is None:
        raise HTTPException(status_code=409, detail={"code": "no_session", "message": "无活跃会话"})

    resp = await controller.eval_agent.handle_request(
        AgentRequest(type="generate_eval", payload={}, session=session)
    )
    if not resp.success:
        raise HTTPException(status_code=500, detail={"code": "eval_error", "message": resp.error})
    try:
        await controller.close_session()
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


@router.delete("/candidates/{candidate_id}")
async def delete_candidate(request: Request, candidate_id: str):
    bind_op("delete_candidate")
    memory = _memory(request)
    candidate = await memory.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "候选人不存在"})

    # 若当前活跃会话正在使用该候选人，拒绝删除
    controller = _controller(request)
    session = await controller.get_session() if controller else None
    if session is not None and session.candidate.id == candidate_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "candidate_in_use", "message": "候选人当前正在面试中，无法删除"},
        )

    await memory.delete_candidate(candidate_id)
    logger.info("delete_candidate done candidate_id=%s", candidate_id)
    return {"deleted": True, "candidate_id": candidate_id}


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
    controller = _controller(request)
    session = await controller.get_session() if controller else None
    if session is None:
        return {"session": None}
    cm = getattr(request.app.state, "context_manager", None)
    ctx = cm.get_context() if cm is not None else None
    return {
        "session": {
            "id": session.id,
            "stage": session.stage.value,
            "active_agent": "main",
            "candidate_id": session.candidate.id,
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
