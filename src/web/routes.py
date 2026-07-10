"""REST API 路由 — 业务逻辑委托给 MainAgent / InterviewController / MemoryModule。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

# M9-2: PDF 上传大小上限，防止恶意大文件 OOM；简历 20MB 通常足够
_UPLOAD_MAX_BYTES = 20 * 1024 * 1024

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from src.logging import bind_op, bind_session_id

from ..agents.base import AgentRequest
from ..models.exceptions import SessionError, StorageError
from ..models.session import InterviewStage
from .schemas import (
    CandidateSelectRequest,
    ChatRequest,
    ResolveDuplicateRequest,
    StartInterviewRequest,
    SwitchAgentRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _controller(request: Request):
    return request.app.state.controller


def _require_controller(request: Request):
    """FastAPI 依赖：保证 controller 已初始化，否则 503。"""
    controller = getattr(request.app.state, "controller", None)
    if controller is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "not_ready", "message": "服务未初始化"},
        )
    return controller


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
    return HTTPException(
        status_code=409, detail={"code": "session_error", "message": str(exc)}
    )


# ── chat (MainAgent) ──────────────────────────────────────────────────────────


@router.post("/chat")
async def chat(request: Request, body: ChatRequest):
    """接收用户消息，流式转发到 MainAgent。"""
    bind_op("chat")
    main_agent = _main_agent(request)
    if main_agent is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "not_ready", "message": "MainAgent 未初始化"},
        )

    async def _stream():
        async for chunk in main_agent.handle_chat(body.message):
            if isinstance(chunk, str):
                yield f"data: {json.dumps({'type': 'delta', 'delta': chunk}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
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
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "候选人不存在"}
        )

    # Ensure session exists with this candidate
    if controller is not None:
        session = await controller.get_session()
        if session is None or session.candidate.id != body.candidate_id:
            try:
                session = await controller.create_session(body.candidate_id)
            except SessionError as exc:
                raise _session_err(exc) from None

    # Load interview brief
    brief: str = ""
    if controller is not None:
        session = await controller.get_session()
        if session and session.interview_brief:
            brief = session.interview_brief
    if not brief:
        brief = memory.get_brief(body.candidate_id)

    # Load candidate history summary (best-effort: failure must not block candidate selection)
    history_summary: str | None = None
    try:
        candidate_history = await memory.get_candidate_history(body.candidate_id)
        if candidate_history:
            history_summary = candidate_history.history_summary
    except Exception:
        logger.warning(
            "Failed to load candidate history for %s, skipping",
            body.candidate_id,
            exc_info=True,
        )

    # Update MainAgent context
    if main_agent is not None:
        main_agent.set_candidate_context(
            candidate,
            interview_brief=brief,
            history_summary=history_summary,
        )

    resume_markdown = await memory.get_resume_markdown(body.candidate_id)

    latest_report = await memory.get_latest_eval_report(body.candidate_id)
    eval_report = _to_dict(latest_report) if latest_report is not None else None

    logger.info(
        "candidate_select done candidate_id=%s name=%s",
        body.candidate_id,
        candidate.name,
    )
    return {
        "candidate_id": body.candidate_id,
        "profile": _to_dict(candidate),
        "brief": brief,
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
):
    """上传 PDF 简历：仅保存文件，返回 file_path 和 safe_stem。解析由前端确认后触发。

    不做候选人去重检查——去重已迁移到解析完成后按真实姓名比对
    （见 `src.tools.dispatch_to_agent._apply_side_effects` 的 parse_done 分支
    与 `POST /api/resume/resolve-duplicate`）。
    """
    bind_op("upload_resume")
    start = time.perf_counter()
    filename = file.filename or "resume.pdf"
    controller = _controller(request)

    suffix = os.path.splitext(filename)[1].lower() or ".pdf"
    if suffix not in {".pdf"}:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_file_type",
                "message": f"仅支持 PDF 格式简历，收到的文件类型为 {suffix!r}",
            },
        )

    safe_stem = _safe_stem(filename)

    # Ensure session exists
    session = await controller.get_session() if controller else None

    # 拒绝在面试进行中或评价中上传新简历，避免破坏现场 session 数据
    if session is not None and session.stage in (
        InterviewStage.INTERVIEWING,
        InterviewStage.EVALUATING,
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "interview_in_progress",
                "message": "当前面试进行中或正在评价，无法上传新简历",
            },
        )

    if session is None and controller is not None:
        try:
            session = await controller.create_session(candidate_id)
        except SessionError as exc:
            raise HTTPException(
                status_code=404, detail={"code": "not_found", "message": str(exc)}
            ) from None
    elif session is not None and candidate_id and session.candidate.id != candidate_id:
        try:
            session = await controller.create_session(candidate_id)
        except SessionError as exc:
            raise HTTPException(
                status_code=404, detail={"code": "not_found", "message": str(exc)}
            ) from None
    elif session is not None and not candidate_id:
        # 仅当当前 session 已绑定其它候选人（有姓名或 PDF 路径）时才重建，
        # 否则复用现有空 session 避免清空已积累的对话上下文。
        has_existing_candidate = bool(
            session.candidate.name or session.candidate.resume_pdf
        )
        if has_existing_candidate:
            session = await controller.create_session(None)

    if session:
        bind_session_id(session.id)

    logger.info(
        "upload_resume start filename=%r safe_stem=%r candidate_id=%s",
        filename,
        safe_stem,
        candidate_id or (session.candidate.id if session else ""),
    )

    # M9-2: 流式写入 + 限制 20MB —— 防止恶意/异常大文件 OOM
    resumes_dir = Path("resumes")
    resumes_dir.mkdir(exist_ok=True)
    pdf_path = resumes_dir / f"{safe_stem}.pdf"
    max_bytes = _UPLOAD_MAX_BYTES
    written = 0
    try:
        with open(pdf_path, "wb") as f:
            while True:
                chunk = await file.read(8192)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    f.close()
                    try:
                        pdf_path.unlink()
                    except OSError:
                        pass
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "code": "file_too_large",
                            "message": f"PDF 大小超过 {max_bytes // (1024 * 1024)}MB 上限",
                        },
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        try:
            pdf_path.unlink()
        except OSError:
            pass
        logger.exception("upload_resume: write failed")
        raise HTTPException(
            status_code=500,
            detail={"code": "write_failed", "message": f"保存文件失败：{exc}"},
        ) from exc

    if session:
        session.candidate.resume_pdf = str(pdf_path)

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "upload_resume saved file_path=%s bytes=%d elapsed_ms=%.1f",
        pdf_path,
        written,
        elapsed_ms,
    )

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
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "候选人不存在"}
        )
    controller = _controller(request)
    session = None
    if controller:
        s = await controller.get_session()
        if s and s.candidate.id == candidate_id:
            session = s
    brief: str = session.interview_brief if session and session.interview_brief else ""
    if not brief:
        brief = memory.get_brief(candidate_id)
    resume_markdown = await memory.get_resume_markdown(candidate_id)
    return {
        "candidate_id": candidate_id,
        "profile": _to_dict(candidate),
        "brief": brief,
        "resume_markdown": resume_markdown,
    }


@router.post("/resume/resolve-duplicate")
async def resolve_duplicate(request: Request, body: ResolveDuplicateRequest):
    """处理 parse_done 判重命中后的面试官决议：覆盖 / 同时保留两份 / 取消本次导入。

    `pending_id` 来自 `/api/chat` SSE 流中的 `duplicate_candidate` 事件。
    """
    bind_op("resolve_duplicate")
    from ..tools._context import ctx as tool_ctx

    pending = tool_ctx.pending_duplicates.get(body.pending_id)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "pending_not_found",
                "message": "待处理的重名候选人记录不存在或已处理",
            },
        )

    memory = _memory(request)
    controller = _controller(request)
    session = await controller.get_session() if controller else None
    same_session = session is not None and session.id == pending.session_id

    if body.action == "cancel":
        tool_ctx.pending_duplicates.pop(body.pending_id, None)
        return {"action": "cancel", "pending_id": body.pending_id}

    # 拷贝而非引用，避免 save 失败时污染 ctx.pending_duplicates 里的活跃对象
    profile = dataclasses.replace(pending.new_profile)
    if body.action == "overwrite":
        profile.id = pending.existing_candidate_id

    try:
        candidate_id = await memory.save_candidate(profile, pending.resume_markdown)
    except Exception as exc:
        logger.exception("resolve_duplicate: save_candidate failed")
        raise HTTPException(
            status_code=500,
            detail={"code": "save_failed", "message": f"保存候选人档案失败：{exc}"},
        ) from exc

    profile.resume_content = pending.resume_markdown
    if same_session:
        session.candidate = profile
        session.metadata.candidate_id = candidate_id
        main_agent = _main_agent(request)
        if main_agent is not None:
            main_agent.set_candidate_context(
                profile, interview_brief=session.interview_brief
            )

    tool_ctx.pending_duplicates.pop(body.pending_id, None)
    logger.info(
        "resolve_duplicate done action=%s candidate_id=%s", body.action, candidate_id
    )
    return {
        "action": body.action,
        "candidate_id": candidate_id,
        "candidate_name": profile.name,
        "session_id": session.id if same_session else None,
    }


# ── brief ─────────────────────────────────────────────────────────────────────


@router.get("/interview/brief")
async def get_brief(
    request: Request,
    candidate_id: str = Query(...),
    controller=Depends(_require_controller),
):
    session = await controller.get_session()
    brief: str = ""
    if session and session.candidate.id == candidate_id:
        brief = session.interview_brief
    if not brief:
        memory = _memory(request)
        brief = memory.get_brief(candidate_id)
    return {"brief": brief}


# ── interview lifecycle ───────────────────────────────────────────────────────


@router.post("/interview/start")
async def start_interview(
    body: StartInterviewRequest,
    controller=Depends(_require_controller),
):
    bind_op("start_interview")
    try:
        session = await controller.get_session()
        if session is None:
            session = await controller.create_session(body.candidate_id)
        await controller.start_interview()
    except SessionError as exc:
        raise _session_err(exc) from None

    session.metadata.trigger_mode = body.trigger_mode
    if body.trigger_mode != "auto":
        trigger = controller.interview_agent.suggestion_trigger
        if trigger is not None:
            try:
                trigger.set_mode(body.trigger_mode)
            except ValueError:
                logger.warning(
                    "start_interview: invalid trigger_mode %r, falling back to auto",
                    body.trigger_mode,
                )
                session.metadata.trigger_mode = "auto"
    bind_session_id(session.id)
    logger.info(
        "start_interview done session_id=%s trigger_mode=%s stage=%s",
        session.id,
        session.metadata.trigger_mode,
        session.stage.value,
    )
    return {"session_id": session.id, "stage": session.stage.value}


@router.post("/interview/stop")
async def stop_interview(controller=Depends(_require_controller)):
    bind_op("stop_interview")
    session = await controller.get_session()
    if session is None:
        raise HTTPException(
            status_code=409, detail={"code": "no_session", "message": "无活跃会话"}
        )
    try:
        await controller.stop_interview()
    except SessionError as exc:
        raise _session_err(exc) from None
    bind_session_id(session.id)
    total_rounds = len(session.rounds)
    logger.info(
        "stop_interview done session_id=%s total_rounds=%d", session.id, total_rounds
    )
    return {
        "session_id": session.id,
        "stage": session.stage.value,
        "total_rounds": total_rounds,
    }


# ── session switch (legacy — maps to controller operations) ───────────────────


@router.post("/session/switch")
async def switch_agent(
    body: SwitchAgentRequest,
    controller=Depends(_require_controller),
):
    try:
        if body.target_agent == "interview":
            await controller.start_interview()
        elif body.target_agent == "eval":
            await controller.stop_interview()
        else:
            raise SessionError(f"不支持的目标 Agent: {body.target_agent!r}")
    except SessionError as exc:
        raise _session_err(exc) from None
    return {"stage": controller.stage.value, "active_agent": "main"}


# ── suggestion ────────────────────────────────────────────────────────────────


@router.post("/interview/suggest")
async def trigger_suggest(controller=Depends(_require_controller)):
    session = await controller.get_session()
    if session is None:
        raise HTTPException(
            status_code=409, detail={"code": "no_session", "message": "无活跃会话"}
        )
    from ..utils.metrics import Metrics

    Metrics.get().record_suggestion_trigger("manual")
    resp = await controller.interview_agent.handle_request(
        AgentRequest(type="trigger_suggestion", payload={}, session=session)
    )
    if not resp.success:
        raise HTTPException(
            status_code=409, detail={"code": "trigger_error", "message": resp.error}
        )
    return resp.data


# ── eval ─────────────────────────────────────────────────────────────────────


@router.get("/interview/eval")
async def get_eval(request: Request, interview_id: str | None = None):
    controller = _controller(request)
    memory = _memory(request)

    if interview_id:
        report = await memory.get_eval_report(interview_id)
        if report is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "评价报告不存在"},
            )
        return {"report": _to_dict(report)}

    session = await controller.get_session() if controller else None
    if session is None:
        raise HTTPException(
            status_code=409, detail={"code": "no_session", "message": "无活跃会话"}
        )

    eval_resp: Any = None
    eval_error: str | None = None
    try:
        resp = await controller.eval_agent.handle_request(
            AgentRequest(type="generate_eval", payload={}, session=session)
        )
        if not resp.success:
            eval_error = resp.error
        else:
            eval_resp = resp
    except Exception as exc:
        eval_error = str(exc)
        logger.exception("get_eval: eval_agent.handle_request raised")
    finally:
        # L5-5: close_session 失败重试 3 次，仍失败时附带 warning（评价数据不重新生成）。
        close_warning: str | None = None
        for attempt in range(3):
            try:
                await controller.close_session()
                close_warning = None
                break
            except Exception as exc:
                logger.warning(
                    "get_eval: close_session attempt %d/3 failed: %s",
                    attempt + 1,
                    exc,
                    exc_info=(attempt == 2),
                )
                close_warning = "评价已生成，但会话关闭失败（已重试3次）。请刷新页面或重启服务再开始下一次面试。"
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))

    if eval_error is not None:
        raise HTTPException(
            status_code=500, detail={"code": "eval_error", "message": eval_error}
        )

    # M4-2: EvalAgent 在持久化降级到 eval_orphans 时会在 data.save_warning 里写说明。
    save_warning: str | None = eval_resp.data.get("save_warning")
    result: dict[str, Any] = {"report": _to_dict(eval_resp.data["report"])}
    warnings = [w for w in (save_warning, close_warning) if w]
    if warnings:
        result["warning"] = " | ".join(warnings)
    return result


@router.get("/interview/{interview_id}/report/export")
async def export_report_pdf(interview_id: str, request: Request):
    """将指定面试的评价报告导出为 PDF 文件供浏览器下载。"""
    from fastapi.responses import Response

    from ..utils.pdf_export import build_report_pdf

    memory = _memory(request)
    report = await memory.get_eval_report(interview_id)
    if report is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "评价报告不存在"}
        )

    candidate_name = ""
    if report.candidate_id:
        candidate = await memory.get_candidate(report.candidate_id)
        if candidate:
            candidate_name = candidate.name or ""

    pdf_bytes = build_report_pdf(report, candidate_name)
    filename = f"eval_report_{interview_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── structured questions ──────────────────────────────────────────────────────


@router.get("/interview/questions")
async def get_questions(request: Request, candidate_id: str = Query(...)):
    memory = _memory(request)
    questions = memory.get_questions(candidate_id)
    return {"questions": questions}


@router.patch("/interview/questions/{question_id}")
async def update_question(
    request: Request, question_id: str, candidate_id: str = Query(...)
):
    body = await request.json()
    covered = bool(body.get("covered", False))
    memory = _memory(request)
    found = memory.update_question_coverage(
        candidate_id, question_id, covered, covered_by="manual"
    )
    if not found:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "问题不存在"}
        )
    return {"updated": True, "question_id": question_id, "covered": covered}


def _build_coverage_prompt(round_text: str, uncovered: list[dict]) -> str:
    """构建问题覆盖判定 prompt（宽松标准：主题被实质讨论即算覆盖）。"""
    q_list = "\n".join(
        f'{i + 1}. [{q["id"]}] {q["question"]}（考察：{q["focus"]}）'
        for i, q in enumerate(uncovered)
    )
    return (
        f"以下是面试对话记录：\n{round_text}\n\n"
        f"以下是尚未覆盖的面试问题清单：\n{q_list}\n\n"
        "请分析对话内容，判断哪些问题已被对话覆盖。\n"
        "判定标准（宽松）：只要问题的考察主题在对话中被实质性讨论过即视为已覆盖——"
        "包括面试官换了措辞提问、只问到问题的一部分、或候选人主动谈及该主题并给出了"
        "具体内容；不要求对话与问题原文逐字匹配。\n"
        '以 JSON 数组返回已覆盖问题的 ID 列表，格式：["id1", "id2"]，'
        "未覆盖任何问题则返回 []。"
    )


async def _auto_check_coverage(
    memory,
    llm_client,
    candidate_id: str,
    session,
):
    """自动检测问题覆盖情况（后端触发）。"""
    try:
        questions = memory.get_questions(candidate_id)
        if not questions:
            return

        uncovered = [q for q in questions if not q.get("covered")]
        if not uncovered:
            return

        # 获取完整对话历史
        rounds = session.rounds
        if not rounds:
            return

        # 构建对话文本
        import json as _json

        from ..models.message import Message

        round_text = "\n\n".join(
            f"面试官: {r.interviewer_text}\n候选人: {r.candidate_text}" for r in rounds
        )

        prompt = _build_coverage_prompt(round_text, uncovered)

        resp = await llm_client.chat(
            [Message(role="user", content=prompt)], temperature=0.1
        )
        raw = resp.content or ""
        start, end = raw.find("["), raw.rfind("]")
        if start != -1 and end != -1:
            covered_ids = _json.loads(raw[start : end + 1])
            logger.info(
                "Auto coverage check: candidate=%s covered_ids=%s",
                candidate_id,
                covered_ids,
            )
            for qid in covered_ids:
                memory.update_question_coverage(
                    candidate_id, str(qid), True, covered_by="auto"
                )

    except Exception as e:
        # 静默失败，不影响主流程
        logger.warning(f"Auto coverage check failed: {e}")


@router.post("/interview/questions/check-coverage")
async def check_question_coverage(request: Request):
    """用 LLM 分析一轮对话，自动标记已覆盖问题。"""
    body = await request.json()
    candidate_id: str = body.get("candidate_id", "")
    round_text: str = body.get("round_text", "")
    if not candidate_id or not round_text:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "missing_fields",
                "message": "candidate_id 和 round_text 为必填项",
            },
        )

    memory = _memory(request)
    questions = memory.get_questions(candidate_id)
    uncovered = [q for q in questions if not q.get("covered")]
    if not uncovered:
        return {"updated": [], "questions": questions}

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return {"updated": [], "questions": questions}

    updated_ids: list[str] = []
    try:
        import json as _json

        from ..llm.client import OpenAICompatibleClient
        from ..models.message import Message

        # 使用注入的 llm_client，fallback 到直接实例化
        llm = getattr(request.app.state, "llm_client", None)
        if not llm:
            llm = OpenAICompatibleClient(settings)

        prompt = _build_coverage_prompt(round_text, uncovered)
        resp = await llm.chat([Message(role="user", content=prompt)], temperature=0.1)
        raw = resp.content or ""
        start, end = raw.find("["), raw.rfind("]")
        if start != -1 and end != -1:
            covered_ids = _json.loads(raw[start : end + 1])
            logger.info(
                "Coverage check: candidate=%s covered_ids=%s",
                candidate_id,
                covered_ids,
            )
            for qid in covered_ids:
                if memory.update_question_coverage(
                    candidate_id, str(qid), True, covered_by="auto"
                ):
                    updated_ids.append(str(qid))
    except Exception:
        logger.exception("check_question_coverage: LLM call failed")

    return {"updated": updated_ids, "questions": memory.get_questions(candidate_id)}


# ── candidates ────────────────────────────────────────────────────────────────


@router.get("/candidates")
async def list_candidates(
    request: Request,
    keyword: str = "",
    limit: int = 20,
    offset: int = 0,
):
    memory = _memory(request)
    total = await memory.count_candidates(keyword=keyword)
    candidates = await memory.search_candidates(
        keyword=keyword, limit=limit, offset=offset
    )
    return {
        "candidates": [_to_dict(c) for c in candidates],
        "total": total,
    }


@router.get("/candidates/{candidate_id}/history")
async def get_candidate_history(request: Request, candidate_id: str):
    memory = _memory(request)
    candidate = await memory.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "候选人不存在"}
        )
    history = await memory.get_candidate_history(candidate_id)
    return {
        "candidate": _to_dict(candidate),
        "interviews": _to_dict(history.past_interviews) if history else [],
    }


@router.get("/candidates/compare")
async def compare_candidates(request: Request, ids: str = ""):
    """横向对比多名候选人的 EvalReport，返回评分表格和 LLM 生成的对比摘要。

    ids: 逗号分隔的候选人 ID，2–5 个。
    """
    bind_op("compare_candidates")
    if not ids:
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_ids", "message": "请提供候选人 ID（ids 参数）"},
        )

    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if len(id_list) < 2:
        raise HTTPException(
            status_code=422,
            detail={"code": "too_few", "message": "至少选择 2 名候选人进行对比"},
        )
    if len(id_list) > 5:
        raise HTTPException(
            status_code=422,
            detail={"code": "too_many", "message": "最多对比 5 名候选人"},
        )

    memory = _memory(request)
    settings = getattr(request.app.state, "settings", None)

    rows: list[dict] = []
    missing_report: list[str] = []

    for cid in id_list:
        candidate = await memory.get_candidate(cid)
        if candidate is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": f"候选人 {cid} 不存在"},
            )
        report = await memory.get_latest_eval_report(cid)
        rows.append(
            {
                "id": cid,
                "name": candidate.name or cid,
                "report": _to_dict(report) if report else None,
            }
        )
        if report is None:
            missing_report.append(candidate.name or cid)

    # 构造评分对比表格
    all_dims: list[str] = []
    for row in rows:
        if row["report"]:
            for d in row["report"].get("dimensions", []):
                dim_name = d.get("dimension", "")
                if dim_name and dim_name not in all_dims:
                    all_dims.append(dim_name)

    score_table: list[dict] = []
    for row in rows:
        entry: dict = {"name": row["name"], "overall_score": None, "dimensions": {}}
        if row["report"]:
            entry["overall_score"] = row["report"].get("overall_score")
            for d in row["report"].get("dimensions", []):
                entry["dimensions"][d.get("dimension", "")] = d.get("score")
        score_table.append(entry)

    # LLM 生成对比摘要
    llm_summary = ""
    try:
        from ..llm.client import OpenAICompatibleClient
        from ..models.message import Message

        if settings is None:
            raise RuntimeError("settings not available")

        # 使用注入的 llm_client，fallback 到直接实例化
        llm = getattr(request.app.state, "llm_client", None)
        if not llm:
            llm = OpenAICompatibleClient(settings)

        report_texts = []
        for row in rows:
            if row["report"]:
                r = row["report"]
                dims_text = "; ".join(
                    f"{d.get('dimension')}={d.get('score')}"
                    for d in r.get("dimensions", [])
                )
                report_texts.append(
                    f"【{row['name']}】综合={r.get('overall_score')} | {dims_text} | "
                    f"优势：{', '.join(r.get('strengths', [])[:2])} | "
                    f"劣势：{', '.join(r.get('weaknesses', [])[:2])} | "
                    f"建议：{r.get('recommendation', '')}"
                )
            else:
                report_texts.append(f"【{row['name']}】暂无评价报告")

        prompt = (
            "以下是多名候选人的面试评价摘要，请生成横向对比分析，包括：\n"
            "1. 各候选人综合能力排序（附简短理由）\n"
            "2. 各自核心优势与短板对比\n"
            "3. 岗位匹配度建议（谁最适合录用）\n\n" + "\n".join(report_texts)
        )

        resp = await llm.chat([Message(role="user", content=prompt)], temperature=0.3)
        llm_summary = resp.content or ""
    except Exception:
        logger.exception("compare_candidates: LLM summary failed")
        llm_summary = "（对比摘要生成失败，请查看评分表格）"

    return {
        "candidates": [
            {"id": r["id"], "name": r["name"], "has_report": r["report"] is not None}
            for r in rows
        ],
        "missing_report": missing_report,
        "dimensions": all_dims,
        "score_table": score_table,
        "llm_summary": llm_summary,
    }


@router.delete("/candidates/{candidate_id}")
async def delete_candidate(request: Request, candidate_id: str):
    bind_op("delete_candidate")
    memory = _memory(request)
    candidate = await memory.get_candidate(candidate_id)
    if candidate is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "候选人不存在"}
        )

    # 若当前活跃会话正在使用该候选人，拒绝删除
    controller = _controller(request)
    session = await controller.get_session() if controller else None
    if session is not None and session.candidate.id == candidate_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "candidate_in_use",
                "message": "候选人当前正在面试中，无法删除",
            },
        )

    await memory.delete_candidate(candidate_id)
    logger.info("delete_candidate done candidate_id=%s", candidate_id)
    return {"deleted": True, "candidate_id": candidate_id}


# ── recovery (rounds.jsonl WAL 残留处理) ──────────────────────────────────────


@router.get("/recovery/scan")
async def scan_recovery(request: Request):
    """列出上次进程崩溃前未 finish_interview 的残留 WAL（rounds.jsonl）。"""
    bind_op("recovery_scan")
    memory = _memory(request)
    orphans = await memory.scan_orphan_wal()
    return {"orphans": orphans, "count": len(orphans)}


@router.post("/recovery/finish")
async def finish_recovery(request: Request):
    """从 WAL 恢复指定面试：重建 rounds → 写 transcript.md → 归档 WAL。

    body: {"candidate_id": "...", "interview_id": "..."}
    """
    bind_op("recovery_finish")
    payload = await request.json()
    candidate_id = str(payload.get("candidate_id", "")).strip()
    interview_id = str(payload.get("interview_id", "")).strip()
    if not candidate_id or not interview_id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "bad_request",
                "message": "缺少 candidate_id / interview_id",
            },
        )
    memory = _memory(request)
    try:
        recovered = await memory.recover_interview_from_wal(candidate_id, interview_id)
    except (SessionError, StorageError) as exc:
        # StorageError 在 WAL 不存在时抛出，语义上属于 404
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": str(exc)}
        ) from exc
    except Exception as exc:
        logger.exception(
            "finish_recovery failed candidate=%s interview=%s",
            candidate_id,
            interview_id,
        )
        raise HTTPException(
            status_code=500, detail={"code": "recovery_failed", "message": str(exc)}
        ) from exc
    return {
        "recovered_rounds": recovered,
        "candidate_id": candidate_id,
        "interview_id": interview_id,
    }


@router.post("/recovery/discard")
async def discard_recovery(request: Request):
    """丢弃残留 WAL（用户确认不需要恢复时）。"""
    bind_op("recovery_discard")
    payload = await request.json()
    candidate_id = str(payload.get("candidate_id", "")).strip()
    interview_id = str(payload.get("interview_id", "")).strip()
    if not candidate_id or not interview_id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "bad_request",
                "message": "缺少 candidate_id / interview_id",
            },
        )
    memory = _memory(request)
    deleted = await memory.discard_orphan_wal(candidate_id, interview_id)
    return {
        "deleted": deleted,
        "candidate_id": candidate_id,
        "interview_id": interview_id,
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
        (
            os.path.join(rounds_dir, f"round_{round_number:03d}_{source}.wav")
            if source
            else None
        ),
        os.path.join(rounds_dir, f"round_{round_number:03d}_candidate.wav"),
        os.path.join(rounds_dir, f"round_{round_number:03d}_interviewer.wav"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return FileResponse(path, media_type="audio/wav")
    raise HTTPException(
        status_code=404, detail={"code": "not_found", "message": "录音文件不存在"}
    )


# ── health / metrics ─────────────────────────────────────────────────────────


@router.get("/health")
async def health(request: Request):
    """S-10: 轻量健康探针，供 Docker healthcheck / 进程守护脚本使用。

    controller 或 memory 任意一个未就绪时返回 503，以便 Docker/K8s 探针
    在服务真正可用前不将流量路由过来。
    """
    controller = getattr(request.app.state, "controller", None)
    memory = getattr(request.app.state, "memory_module", None)
    ready = controller is not None and memory is not None
    payload = {
        "status": "ok" if ready else "not_ready",
        "controller": controller is not None,
        "memory": memory is not None,
    }
    if not ready:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content=payload)
    return payload


@router.get("/metrics")
async def get_metrics():
    """S-11: 返回进程级累积 LLM 指标（token / 请求次数 / 延迟百分位数）。"""
    from ..utils.metrics import Metrics

    return Metrics.get().to_dict()


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


@router.get("/interview/last-round")
async def get_last_round(controller=Depends(_require_controller)):
    """返回当前会话最近一轮对话的文本（用于覆盖检测）。"""
    session = await controller.get_session()
    if session is None or not session.rounds:
        return {"round_text": ""}
    last = session.rounds[-1]
    text = f"面试官：{last.interviewer_text}\n候选人：{last.candidate_text}"
    return {"round_text": text}
