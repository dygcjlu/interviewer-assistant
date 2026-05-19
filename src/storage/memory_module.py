"""MemoryModule：短期 / 长期记忆统一接口。

短期记忆由运行时的 ``InterviewSession`` 对象承载（在 ``agents/`` 层维护）；
长期记忆由本模块通过 Repository 层落盘到 SQLite。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..models.candidate import (
    CandidateProfile,
    Education,
    ProjectExperience,
    WorkExperience,
)
from ..models.evaluation import DimensionScore, EvalReport
from ..models.exceptions import StorageError
from ..models.session import ConversationRound, InterviewSession

from .database import Database
from .repositories import (
    CandidateRepository,
    EvalReportRepository,
    InterviewRepository,
    RoundRepository,
    TokenUsageRepository,
)

logger = logging.getLogger(__name__)


# ─── 辅助数据结构 ──────────────────────────────────────────────────────


@dataclass
class InterviewSummary:
    interview_id: str
    date: datetime
    overall_score: float | None
    recommendation: str | None
    key_findings: str


@dataclass
class CandidateHistory:
    past_interviews: list[InterviewSummary]
    history_summary: str


@dataclass
class RecordingPaths:
    full_candidate: str
    full_interviewer: str


@dataclass
class InterviewDetail:
    interview_id: str
    candidate_id: str
    start_time: datetime
    end_time: datetime | None
    rounds: list[ConversationRound] = field(default_factory=list)
    eval_report: EvalReport | None = None
    recording_paths: RecordingPaths | None = None


# ─── 序列化辅助 ────────────────────────────────────────────────────────


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Invalid datetime string in DB: %r", value)
        return None


def _profile_to_json(profile: CandidateProfile) -> dict[str, Any]:
    return {
        "email": profile.email,
        "phone": profile.phone,
        "age": profile.age,
        "resume_markdown_path": profile.resume_markdown_path,
        "education": [
            {
                "school": e.school,
                "degree": e.degree,
                "major": e.major,
                "start_year": e.start_year,
                "end_year": e.end_year,
            }
            for e in profile.education
        ],
        "work_experience": [
            {
                "company": w.company,
                "title": w.title,
                "duration": w.duration,
                "description": w.description,
            }
            for w in profile.work_experience
        ],
        "skills": list(profile.skills),
        "projects": [
            {
                "name": p.name,
                "role": p.role,
                "tech_stack": list(p.tech_stack),
                "description": p.description,
                "highlights": list(p.highlights),
            }
            for p in profile.projects
        ],
        "resume_summary": profile.resume_summary,
        "resume_pdf_path": profile.resume_pdf_path,
        "years_of_experience": profile.years_of_experience,
        "current_position": profile.current_position,
    }


def _profile_from_row(row: dict[str, Any]) -> CandidateProfile:
    try:
        payload = json.loads(row["profile_json"] or "{}")
    except json.JSONDecodeError:
        logger.warning(
            "Corrupt profile_json for candidate %s; falling back to empty",
            row.get("id"),
        )
        payload = {}

    education = [
        Education(
            school=e.get("school", ""),
            degree=e.get("degree", ""),
            major=e.get("major", ""),
            start_year=e.get("start_year"),
            end_year=e.get("end_year"),
        )
        for e in payload.get("education", [])
    ]
    work_experience = [
        WorkExperience(
            company=w.get("company", ""),
            title=w.get("title", ""),
            duration=w.get("duration", ""),
            description=w.get("description", ""),
        )
        for w in payload.get("work_experience", [])
    ]
    projects = [
        ProjectExperience(
            name=p.get("name", ""),
            role=p.get("role", ""),
            tech_stack=list(p.get("tech_stack", [])),
            description=p.get("description", ""),
            highlights=list(p.get("highlights", [])),
        )
        for p in payload.get("projects", [])
    ]

    age_val = payload.get("age")
    age: int | None = None
    if age_val is not None:
        try:
            age = int(age_val)
        except (TypeError, ValueError):
            pass

    yoe_val = payload.get("years_of_experience")
    years_of_experience: int | None = None
    if yoe_val is not None:
        try:
            years_of_experience = int(yoe_val)
        except (TypeError, ValueError):
            pass

    return CandidateProfile(
        id=row["id"],
        name=row["name"],
        email=payload.get("email"),
        phone=payload.get("phone"),
        age=age,
        education=education,
        work_experience=work_experience,
        skills=list(payload.get("skills", [])),
        projects=projects,
        resume_text=row.get("resume_text", "") or "",
        resume_summary=payload.get("resume_summary", "") or "",
        resume_markdown_path=payload.get("resume_markdown_path"),
        resume_pdf_path=payload.get("resume_pdf_path"),
        history_summary=None,
        years_of_experience=years_of_experience,
        current_position=payload.get("current_position"),
    )


def _eval_report_from_row(row: dict[str, Any]) -> EvalReport:
    try:
        scores_payload = json.loads(row["scores_json"] or "{}")
    except json.JSONDecodeError:
        logger.warning("Corrupt scores_json for report %s", row.get("id"))
        scores_payload = {}
    try:
        strengths = json.loads(row["strengths"] or "[]")
        weaknesses = json.loads(row["weaknesses"] or "[]")
    except json.JSONDecodeError:
        logger.warning("Corrupt strengths/weaknesses JSON for report %s", row.get("id"))
        strengths, weaknesses = [], []

    dimensions = [
        DimensionScore(
            dimension=d.get("dimension", ""),
            score=float(d.get("score", 0.0)),
            comment=d.get("comment", ""),
            evidence=list(d.get("evidence", [])),
        )
        for d in scores_payload.get("dimensions", [])
    ]
    generated_at = _parse_dt(scores_payload.get("generated_at")) or datetime.now()

    return EvalReport(
        id=row["id"],
        interview_id=row["interview_id"],
        dimensions=dimensions,
        overall_score=float(scores_payload.get("overall_score", 0.0)),
        strengths=strengths if isinstance(strengths, list) else [],
        weaknesses=weaknesses if isinstance(weaknesses, list) else [],
        recommendation=row.get("recommendation") or "",
        summary=row.get("full_report") or "",
        generated_at=generated_at,
    )


def _round_from_row(row: dict[str, Any]) -> ConversationRound:
    return ConversationRound(
        round_number=int(row["round_number"]),
        interviewer_text=row.get("interviewer_text") or "",
        candidate_text=row.get("candidate_text") or "",
        llm_suggestion=row.get("llm_suggestion"),
        interviewer_audio_path=row.get("interviewer_audio_path"),
        candidate_audio_path=row.get("candidate_audio_path"),
        timestamp=_parse_dt(row.get("timestamp")) or datetime.now(),
    )


def _question_plan_to_json(session: InterviewSession) -> str:
    return json.dumps(
        [
            {
                "id": q.id,
                "dimension": q.dimension,
                "question": q.question,
                "follow_ups": list(q.follow_ups),
                "difficulty": q.difficulty,
                "source": q.source,
                "is_covered": q.is_covered,
            }
            for q in session.question_plan
        ],
        ensure_ascii=False,
    )


def _question_plan_from_json(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _format_history_summary(
    candidate_name: str, summaries: list[InterviewSummary]
) -> str:
    if not summaries:
        return ""
    lines: list[str] = [f"候选人 {candidate_name} 历史面试记录："]
    for idx, s in enumerate(summaries, start=1):
        date_str = s.date.strftime("%Y-%m-%d %H:%M")
        score_str = f"{s.overall_score:.1f}/10" if s.overall_score is not None else "未评分"
        rec_str = s.recommendation or "未推荐"
        findings = s.key_findings or "无关键发现"
        lines.append(
            f"\n{idx}. {date_str} — 综合评分 {score_str}，推荐 {rec_str}\n   关键发现: {findings}"
        )
    return "".join(lines)


# ─── 主接口 ───────────────────────────────────────────────────────────


class MemoryModule:
    """统一管理短期/长期记忆的读写。"""

    def __init__(self, storage: Database) -> None:
        self._db = storage
        self._candidates = CandidateRepository(storage)
        self._interviews = InterviewRepository(storage)
        self._rounds = RoundRepository(storage)
        self._eval_reports = EvalReportRepository(storage)
        self._token_usage = TokenUsageRepository(storage)

    # ─── 候选人 ──────────────────────────────────────────────────────

    async def get_candidate(self, candidate_id: str) -> CandidateProfile | None:
        row = await self._candidates.get_by_id(candidate_id)
        if row is None:
            return None
        return _profile_from_row(row)

    async def save_candidate(self, profile: CandidateProfile) -> str:
        start = time.perf_counter()
        candidate_id = profile.id or f"c-{uuid.uuid4().hex[:12]}"
        logger.info("save_candidate start candidate_id=%s name=%r", candidate_id, profile.name or "")
        payload = _profile_to_json(profile)
        await self._candidates.insert(
            id=candidate_id,
            name=profile.name,
            resume_text=profile.resume_text or "",
            profile_json=json.dumps(payload, ensure_ascii=False),
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("save_candidate done candidate_id=%s elapsed_ms=%.1f", candidate_id, elapsed_ms)
        return candidate_id

    async def get_latest_question_plan(self, candidate_id: str) -> list[dict[str, Any]]:
        """从该候选人最近一次面试记录中恢复题目清单。"""
        rows = await self._interviews.get_by_candidate(candidate_id, limit=1)
        if not rows:
            return []
        return _question_plan_from_json(rows[0].get("question_plan_json") or "[]")

    async def get_candidate_by_name(self, name: str) -> CandidateProfile | None:
        """按精确姓名查找候选人（用于上传去重）。"""
        row = await self._candidates.get_by_name_exact(name)
        if row is None:
            return None
        return _profile_from_row(row)

    async def delete_candidate(self, candidate_id: str) -> None:
        """级联删除候选人：DB 记录 + 简历文件（PDF + MD）+ 关联面试和评价数据。"""
        candidate_row = await self._candidates.get_by_id(candidate_id)
        if candidate_row is None:
            return

        # 级联删除关联面试记录（含 ConversationRound / EvalReport / TokenUsage）
        await self._interviews.delete_by_candidate(candidate_id)

        # 删除候选人数据库记录
        await self._candidates.delete(candidate_id)

        # 清理本地简历文件（PDF + MD），路径均记录在 profile_json 中
        try:
            from pathlib import Path as _Path
            payload = json.loads(candidate_row.get("profile_json") or "{}")
            for key in ("resume_markdown_path", "resume_pdf_path"):
                file_path = payload.get(key)
                if file_path:
                    f = _Path(file_path)
                    if f.exists():
                        f.unlink()
                        logger.info("delete_candidate: removed %s %s", key, file_path)
        except Exception:
            logger.warning("delete_candidate: failed to remove resume files for %s", candidate_id)

        logger.info("delete_candidate: done candidate_id=%s", candidate_id)

    async def search_candidates(
        self, keyword: str = "", limit: int = 20
    ) -> list[CandidateProfile]:
        rows = await self._candidates.search_by_name(keyword, limit)
        return [_profile_from_row(r) for r in rows]

    async def get_candidate_history(
        self, candidate_id: str, limit: int = 3
    ) -> CandidateHistory | None:
        candidate_row = await self._candidates.get_by_id(candidate_id)
        if candidate_row is None:
            return None
        interviews = await self._interviews.get_by_candidate(candidate_id, limit)
        if not interviews:
            return None

        summaries: list[InterviewSummary] = []
        for iv in interviews:
            report_row = await self._eval_reports.get_by_interview(iv["id"])
            overall_score: float | None = None
            recommendation: str | None = None
            key_findings = ""
            if report_row is not None:
                try:
                    scores_payload = json.loads(report_row.get("scores_json") or "{}")
                    overall_score = float(scores_payload.get("overall_score")) if scores_payload.get("overall_score") is not None else None
                except (json.JSONDecodeError, TypeError, ValueError):
                    overall_score = None
                recommendation = report_row.get("recommendation")
                key_findings = report_row.get("full_report") or ""

            start_dt = _parse_dt(iv.get("start_time")) or datetime.now()
            summaries.append(
                InterviewSummary(
                    interview_id=iv["id"],
                    date=start_dt,
                    overall_score=overall_score,
                    recommendation=recommendation,
                    key_findings=key_findings,
                )
            )

        text = _format_history_summary(candidate_row["name"], summaries)
        return CandidateHistory(past_interviews=summaries, history_summary=text)

    # ─── 面试记录 ────────────────────────────────────────────────────

    async def save_interview(self, session: InterviewSession) -> None:
        if not session.id:
            raise StorageError("InterviewSession.id is empty; cannot persist")

        start = time.perf_counter()
        logger.info(
            "save_interview start session_id=%s rounds_count=%d",
            session.id,
            len(session.rounds),
        )

        # Upsert candidate first; CandidateRepository uses INSERT OR REPLACE
        try:
            await self.save_candidate(session.candidate)
        except Exception:
            logger.exception(
                "save_interview: failed to persist candidate %s", session.candidate.id
            )

        await self._interviews.insert(
            id=session.id,
            candidate_id=session.metadata.candidate_id,
            start_time=session.metadata.start_time,
            question_plan_json=_question_plan_to_json(session),
            trigger_mode=session.metadata.trigger_mode,
        )

        end_time = session.metadata.end_time
        if end_time is not None:
            await self._interviews.update_on_finish(
                id=session.id,
                end_time=end_time,
                context_summary=session.context_summary,
                recording_candidate_path="",
                recording_interviewer_path="",
            )

        await self._rounds.delete_by_interview(session.id)
        for r in session.rounds:
            await self._rounds.insert(session.id, r)

        prompt = session.metadata.total_prompt_tokens
        completion = session.metadata.total_completion_tokens
        if prompt or completion:
            await self._token_usage.insert(
                interview_id=session.id,
                round_number=session.metadata.total_rounds or len(session.rounds),
                prompt_tokens=prompt,
                completion_tokens=completion,
            )
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "save_interview done session_id=%s rounds_count=%d elapsed_ms=%.1f",
            session.id,
            len(session.rounds),
            elapsed_ms,
        )

    async def get_interview_detail(
        self, interview_id: str
    ) -> InterviewDetail | None:
        row = await self._interviews.get_by_id(interview_id)
        if row is None:
            return None
        rounds = [
            _round_from_row(r)
            for r in await self._rounds.get_by_interview(interview_id)
        ]
        report_row = await self._eval_reports.get_by_interview(interview_id)
        eval_report = _eval_report_from_row(report_row) if report_row else None

        rec_candidate = row.get("full_recording_candidate_path") or ""
        rec_interviewer = row.get("full_recording_interviewer_path") or ""
        recording_paths: RecordingPaths | None = None
        if rec_candidate or rec_interviewer:
            recording_paths = RecordingPaths(
                full_candidate=rec_candidate,
                full_interviewer=rec_interviewer,
            )

        return InterviewDetail(
            interview_id=row["id"],
            candidate_id=row["candidate_id"],
            start_time=_parse_dt(row.get("start_time")) or datetime.now(),
            end_time=_parse_dt(row.get("end_time")),
            rounds=rounds,
            eval_report=eval_report,
            recording_paths=recording_paths,
        )

    # ─── 评价报告 ────────────────────────────────────────────────────

    async def save_eval_report(self, report: EvalReport) -> None:
        await self._eval_reports.insert(report)

    async def get_eval_report(self, interview_id: str) -> EvalReport | None:
        row = await self._eval_reports.get_by_interview(interview_id)
        if row is None:
            return None
        return _eval_report_from_row(row)

    # ─── 面试后记忆整合 ──────────────────────────────────────────────

    async def consolidate_memory(self, session: InterviewSession) -> None:
        candidate_id = session.metadata.candidate_id
        if not candidate_id:
            logger.debug("consolidate_memory skipped: no candidate_id on session")
            return

        report = await self.get_eval_report(session.id)
        if report is None:
            logger.debug(
                "consolidate_memory skipped: no eval report for interview %s",
                session.id,
            )
            return

        candidate_row = await self._candidates.get_by_id(candidate_id)
        if candidate_row is None:
            logger.warning(
                "consolidate_memory: candidate %s not found; skipping",
                candidate_id,
            )
            return

        try:
            existing = json.loads(candidate_row.get("profile_json") or "{}")
        except json.JSONDecodeError:
            existing = {}

        insights: dict[str, Any] = {
            "interview_id": session.id,
            "generated_at": report.generated_at.isoformat(),
            "overall_score": report.overall_score,
            "recommendation": report.recommendation,
            "strengths": list(report.strengths),
            "weaknesses": list(report.weaknesses),
            "dimension_scores": {
                d.dimension: d.score for d in report.dimensions
            },
        }
        existing["last_interview_insights"] = insights

        await self._candidates.update_profile(
            id=candidate_id,
            profile_json=json.dumps(existing, ensure_ascii=False),
        )
        logger.info(
            "consolidate_memory: updated candidate %s with insights from %s",
            candidate_id,
            session.id,
        )