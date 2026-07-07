"""共享纯函数 + 数据类：候选人/面试/评价三个 store 共用的文件格式解析与构建逻辑。

不持有任何状态（无 `self._root` 等），仅做 dataclass 定义、YAML frontmatter
解析/渲染、以及 Markdown 文件内容构建。被 `candidate_store.py`、
`interview_store.py`、`eval_store.py`、`memory_module.py`（Facade）共同引用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import yaml

from ..models.candidate import CandidateProfile
from ..models.evaluation import EvalReport
from ..models.session import ConversationRound, InterviewSession

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


# ─── YAML frontmatter 解析 ────────────────────────────────────────────


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 YAML frontmatter，返回 (meta_dict, body_text)。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    yaml_text = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    try:
        meta = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError:
        logger.warning("Failed to parse YAML frontmatter")
        meta = {}
    return meta, body


def _render_frontmatter(meta: dict[str, Any]) -> str:
    """将 dict 渲染为 YAML frontmatter 块（含首尾 ---）。"""
    return (
        "---\n"
        + yaml.dump(meta, allow_unicode=True, default_flow_style=False)
        + "---\n"
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


# ─── 文件格式构建 ─────────────────────────────────────────────────────


def _build_candidates_index(candidates: list[dict]) -> str:
    meta = {"candidates": candidates}
    lines = [_render_frontmatter(meta), "# 候选人目录\n"]
    lines.append("| 候选人 | ID | 创建时间 | 最近面试 |")
    lines.append("|---|---|---|---|")
    for c in candidates:
        latest = c.get("latest_interview") or "—"
        lines.append(
            f"| {c['name']} | {c['id']} | {c.get('created_at', '')} | {latest} |"
        )
    return "\n".join(lines) + "\n"


def _build_profile_md(profile: CandidateProfile, resume_markdown: str) -> str:
    meta: dict[str, Any] = {
        "id": profile.id,
        "name": profile.name,
        "created_at": profile.created_at or datetime.now().isoformat(),
        "resume_pdf": profile.resume_pdf or "resume.pdf",
    }
    if profile.email:
        meta["email"] = profile.email
    if profile.phone:
        meta["phone"] = profile.phone
    if profile.age is not None:
        meta["age"] = profile.age
    if profile.current_position:
        meta["current_position"] = profile.current_position
    if profile.years_of_experience is not None:
        meta["years_of_experience"] = profile.years_of_experience
    if profile.skills:
        meta["skills"] = list(profile.skills)
    return _render_frontmatter(meta) + "\n" + resume_markdown


def _build_interviews_index(candidate_name: str, interviews: list[dict]) -> str:
    meta = {"interviews": interviews}
    lines = [_render_frontmatter(meta), f"# {candidate_name} · 面试历史\n"]
    lines.append(
        "| 面试 ID | 开始时间 | 状态 | 触发模式 | 综合评分 | 推荐结论 | 关键结论 |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for iv in interviews:
        score = iv.get("overall_score")
        score_str = f"{score}/10" if score is not None else "—"
        rec = iv.get("recommendation") or "—"
        findings = iv.get("key_findings") or "—"
        start = iv.get("start_time", "")[:16].replace("T", " ")
        stage_map = {"interviewing": "进行中", "completed": "已完成", "idle": "未开始"}
        stage_str = stage_map.get(iv.get("stage", ""), iv.get("stage", ""))
        trigger_map = {"auto": "自动", "manual": "手动"}
        trigger_str = trigger_map.get(iv.get("trigger_mode", ""), "自动")
        lines.append(
            f"| {iv['interview_id']} | {start} | {stage_str} | {trigger_str} | {score_str} | {rec} | {findings} |"
        )
    return "\n".join(lines) + "\n"


def _normalize_inline(text: str) -> str:
    """将多行文本压缩为单行（换行符替换为空格），保证 transcript 逐行解析时不丢内容。"""
    return " ".join(text.splitlines()).strip() if text else ""


def _build_transcript_md(session: InterviewSession) -> str:
    candidate = session.candidate
    start_time = session.metadata.start_time
    end_time = session.metadata.end_time or datetime.now()
    meta = {
        "interview_id": session.id,
        "candidate_id": candidate.id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "rounds": len(session.rounds),
    }
    date_str = start_time.strftime("%Y-%m-%d")
    lines = [_render_frontmatter(meta), f"# 面试记录 · {candidate.name} · {date_str}\n"]
    for r in session.rounds:
        ts = r.timestamp.strftime("%H:%M") if r.timestamp else ""
        lines.append(f"\n## Round {r.round_number} · {ts}\n")
        lines.append(f"**面试官：** {_normalize_inline(r.interviewer_text)}\n")
        lines.append(f"**候选人：** {_normalize_inline(r.candidate_text)}\n")
        if r.llm_suggestion:
            lines.append(f"**追问建议：** {_normalize_inline(r.llm_suggestion)}\n")
        lines.append("---")
    return "\n".join(lines) + "\n"


def _build_eval_report_md(report: EvalReport, candidate_name: str) -> str:
    meta: dict[str, Any] = {
        "interview_id": report.interview_id,
        "overall_score": report.overall_score,
        "recommendation": report.recommendation,
        "generated_at": report.generated_at.isoformat(),
    }
    if report.strengths:
        meta["strengths"] = list(report.strengths)
    if report.weaknesses:
        meta["weaknesses"] = list(report.weaknesses)
    if report.dimensions:
        meta["dimensions"] = [
            {"dimension": d.dimension, "score": d.score, "comment": d.comment}
            for d in report.dimensions
        ]
    lines = [_render_frontmatter(meta), f"# 面试评价报告 · {candidate_name}\n"]
    lines.append(f"## 综合评分：{report.overall_score} / 10\n")
    lines.append("## 推荐结论\n")
    lines.append(report.recommendation + "\n")
    if report.summary:
        lines.append(report.summary + "\n")
    if report.strengths:
        lines.append("## 优势\n")
        for s in report.strengths:
            lines.append(f"- {s}")
        lines.append("")
    if report.weaknesses:
        lines.append("## 不足\n")
        for w in report.weaknesses:
            lines.append(f"- {w}")
        lines.append("")
    if report.dimensions:
        lines.append("## 各维度评分\n")
        lines.append("| 维度 | 得分 | 评语 |")
        lines.append("|---|---|---|")
        for d in report.dimensions:
            lines.append(f"| {d.dimension} | {d.score} | {d.comment} |")
    return "\n".join(lines) + "\n"


# ─── 辅助函数 ─────────────────────────────────────────────────────────


def _profile_from_meta(meta: dict) -> CandidateProfile:
    return CandidateProfile(
        id=meta.get("id", ""),
        name=meta.get("name", ""),
        email=meta.get("email"),
        phone=meta.get("phone"),
        age=meta.get("age"),
        current_position=meta.get("current_position"),
        years_of_experience=meta.get("years_of_experience"),
        skills=list(meta.get("skills") or []),
        created_at=str(meta.get("created_at") or ""),
        resume_pdf=str(meta.get("resume_pdf") or ""),
    )


def _format_history_summary(
    candidate_name: str, summaries: list[InterviewSummary]
) -> str:
    if not summaries:
        return ""
    lines = [f"候选人 {candidate_name} 历史面试记录："]
    for idx, s in enumerate(summaries, start=1):
        date_str = s.date.strftime("%Y-%m-%d %H:%M")
        score_str = (
            f"{s.overall_score:.1f}/10" if s.overall_score is not None else "未评分"
        )
        rec_str = s.recommendation or "未推荐"
        findings = s.key_findings or "无关键发现"
        lines.append(
            f"\n{idx}. {date_str} — 综合评分 {score_str}，推荐 {rec_str}\n   关键发现: {findings}"
        )
    return "".join(lines)


def _parse_transcript(text: str) -> list[ConversationRound]:
    """从 transcript.md 正文中解析 ConversationRound 列表（尽力解析）。"""
    _, body = _parse_frontmatter(text)
    rounds: list[ConversationRound] = []
    current: dict[str, str] = {}
    round_number = 0

    for line in body.splitlines():
        if line.startswith("## Round "):
            if current:
                rounds.append(
                    ConversationRound(
                        round_number=round_number,
                        interviewer_text=current.get("interviewer", ""),
                        candidate_text=current.get("candidate", ""),
                        llm_suggestion=current.get("suggestion"),
                        timestamp=datetime.now(),
                    )
                )
            current = {}
            try:
                round_number = int(line.split("Round ")[1].split(" ")[0])
            except (IndexError, ValueError):
                round_number += 1
        elif line.startswith("**面试官：**"):
            current["interviewer"] = line[len("**面试官：**") :].strip()
        elif line.startswith("**候选人：**"):
            current["candidate"] = line[len("**候选人：**") :].strip()
        elif line.startswith("**追问建议：**"):
            current["suggestion"] = line[len("**追问建议：**") :].strip()

    if current:
        rounds.append(
            ConversationRound(
                round_number=round_number,
                interviewer_text=current.get("interviewer", ""),
                candidate_text=current.get("candidate", ""),
                llm_suggestion=current.get("suggestion"),
                timestamp=datetime.now(),
            )
        )
    return rounds
