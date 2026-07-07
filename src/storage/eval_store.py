"""EvalStore：面试评价报告持久化。

目录布局（相对 `root/{candidate_id}/interviews/{interview_id}/`）：
  eval_report.md                    # 评价报告（YAML frontmatter + Markdown 正文）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..models.evaluation import DimensionScore, EvalReport
from ..models.exceptions import StorageError
from ..utils import write_atomic as _write_atomic
from ._store_common import _build_eval_report_md, _parse_dt, _parse_frontmatter
from .candidate_store import CandidateStore
from .interview_store import InterviewStore

logger = logging.getLogger(__name__)


class EvalStore:
    """基于文件系统的面试评价报告管理。"""

    def __init__(
        self,
        root: Path,
        candidate_store: CandidateStore,
        interview_store: InterviewStore,
    ) -> None:
        self._root = root
        self._candidate_store = candidate_store
        self._interview_store = interview_store

    def _eval_report_path(self, candidate_id: str, interview_id: str) -> Path:
        return (
            self._interview_store.interview_dir(candidate_id, interview_id)
            / "eval_report.md"
        )

    async def save_eval_report(self, report: EvalReport) -> None:
        # 通过 interviews/index.md 找 candidate_id
        candidate_id = await self._find_candidate_for_interview(report.interview_id)
        if candidate_id is None:
            # 找不到 candidate_id 时：兜底写入 eval_orphans/，再 raise StorageError 让上层感知。
            # 评价报告是 LLM 多次调用的结晶，绝不能静默丢失。
            orphan_path = self._root / "eval_orphans" / f"{report.interview_id}.md"
            try:
                _write_atomic(orphan_path, _build_eval_report_md(report, ""))
                logger.error(
                    "save_eval_report: candidate not found for interview %s, "
                    "fallback wrote orphan eval to %s",
                    report.interview_id,
                    orphan_path,
                )
            except Exception:
                logger.exception(
                    "save_eval_report: orphan fallback write also failed for interview %s",
                    report.interview_id,
                )
            raise StorageError(
                f"无法定位候选人档案，评价报告已降级写入 eval_orphans/{report.interview_id}.md，"
                f"请检查候选人索引或手动迁移"
            )

        # 写 eval_report.md
        profile_meta = self._candidate_store.read_profile_meta(candidate_id)
        candidate_name = profile_meta.get("name", "") if profile_meta else ""
        path = self._eval_report_path(candidate_id, report.interview_id)
        _write_atomic(path, _build_eval_report_md(report, candidate_name))

        # 更新 interviews/index.md 中的评分和关键结论
        interviews = self._interview_store.read_interviews_index(candidate_id)
        key_findings_parts = []
        if report.strengths:
            key_findings_parts.append("优势: " + "; ".join(report.strengths[:2]))
        if report.weaknesses:
            key_findings_parts.append("不足: " + "; ".join(report.weaknesses[:2]))
        key_findings = "，".join(key_findings_parts) or report.summary[:100]

        found = False
        for iv in interviews:
            if iv.get("interview_id") == report.interview_id:
                iv["overall_score"] = report.overall_score
                iv["recommendation"] = report.recommendation
                iv["key_findings"] = key_findings
                found = True
                break
        if not found:
            _start_time = report.generated_at.isoformat()
            _trigger_mode = "auto"
            try:
                _sj = self._interview_store.session_json_path(
                    candidate_id, report.interview_id
                )
                _sd = json.loads(_sj.read_text(encoding="utf-8"))
                _start_time = _sd.get("start_time", _start_time)
                _trigger_mode = _sd.get("trigger_mode", _trigger_mode)
            except Exception:
                pass
            interviews.insert(
                0,
                {
                    "interview_id": report.interview_id,
                    "start_time": _start_time,
                    "end_time": None,
                    "stage": "completed",
                    "trigger_mode": _trigger_mode,
                    "overall_score": report.overall_score,
                    "recommendation": report.recommendation,
                    "key_findings": key_findings,
                },
            )
        self._interview_store.write_interviews_index(candidate_id, interviews)

        logger.info("save_eval_report done interview_id=%s", report.interview_id)

    async def get_eval_report(
        self, interview_id: str, candidate_id: str | None = None
    ) -> EvalReport | None:
        if candidate_id is None:
            candidate_id = await self._find_candidate_for_interview(interview_id)
        if candidate_id is None:
            return None
        path = self._eval_report_path(candidate_id, interview_id)
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            dimensions = [
                DimensionScore(
                    dimension=d.get("dimension", ""),
                    score=float(d.get("score", 0)),
                    comment=d.get("comment", ""),
                    evidence=list(d.get("evidence") or []),
                )
                for d in (meta.get("dimensions") or [])
                if isinstance(d, dict)
            ]
            return EvalReport(
                id=f"er-{interview_id}",
                interview_id=interview_id,
                dimensions=dimensions,
                overall_score=float(meta.get("overall_score") or 0),
                strengths=list(meta.get("strengths") or []),
                weaknesses=list(meta.get("weaknesses") or []),
                recommendation=meta.get("recommendation") or "",
                summary=body.strip(),
                generated_at=_parse_dt(meta.get("generated_at")) or datetime.now(),
                candidate_id=meta.get("candidate_id", ""),
                question_coverage=meta.get("question_coverage", ""),
            )
        except Exception:
            logger.exception("get_eval_report failed for %s", interview_id)
            return None

    async def get_latest_eval_report(self, candidate_id: str) -> EvalReport | None:
        interviews = self._interview_store.read_interviews_index(candidate_id)
        for iv in interviews:
            report = await self.get_eval_report(
                iv["interview_id"], candidate_id=candidate_id
            )
            if report is not None:
                return report
        return None

    async def _find_candidate_for_interview(self, interview_id: str) -> str | None:
        """扫描 candidates/*/interviews/{interview_id} 找到对应的 candidate_id。"""
        matches = list(self._root.glob(f"*/interviews/{interview_id}"))
        if matches:
            return matches[0].parent.parent.name
        return None
