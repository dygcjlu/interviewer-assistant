"""MemoryModule：基于文件的候选人与面试数据存储。

目录结构：
  candidates/
  ├── index.md                          # 全局候选人目录
  └── {candidate_id}/
      ├── profile.md                    # 候选人档案（YAML frontmatter + 简历全文）
      ├── resume.pdf                    # 原始 PDF
      └── interviews/
          ├── index.md                  # 本候选人的面试历史摘要
          └── {interview_id}/
              ├── questions.md          # 面试问题清单
              ├── transcript.md         # 完整对话记录
              ├── eval_report.md        # 评价报告
              └── session.json          # 会话元数据

`MemoryModule` 本身是一个 Facade：候选人 CRUD 委托给 `CandidateStore`
（`candidate_store.py`），面试生命周期 + WAL 委托给 `InterviewStore`
（`interview_store.py`），评价报告持久化委托给 `EvalStore`（`eval_store.py`）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from ..models.candidate import CandidateProfile
from ..models.evaluation import DimensionScore, EvalReport
from ..models.exceptions import StorageError
from ..models.session import ConversationRound, InterviewSession
from ..utils import write_atomic as _write_atomic
from ._store_common import (
    CandidateHistory,
    InterviewDetail,
    InterviewSummary,
    RecordingPaths,
    _build_candidates_index,
    _build_eval_report_md,
    _build_interviews_index,
    _build_profile_md,
    _build_transcript_md,
    _format_history_summary,
    _normalize_inline,
    _parse_dt,
    _parse_frontmatter,
    _parse_transcript,
    _render_frontmatter,
)
from .candidate_store import CandidateStore

logger = logging.getLogger(__name__)

__all__ = [
    "MemoryModule",
    "CandidateHistory",
    "InterviewDetail",
    "InterviewSummary",
    "RecordingPaths",
    "_build_candidates_index",
    "_build_eval_report_md",
    "_build_profile_md",
    "_build_transcript_md",
    "_normalize_inline",
    "_parse_dt",
    "_parse_frontmatter",
    "_render_frontmatter",
    "_parse_transcript",
]


# ─── 主接口 ───────────────────────────────────────────────────────────


class MemoryModule:
    """基于文件系统的候选人与面试数据管理（Facade）。"""

    def __init__(self, candidates_dir: str = "candidates") -> None:
        self._root = Path(candidates_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / "index.md"
        self._candidates = CandidateStore(self._root)

    # ─── 内部路径工具 ─────────────────────────────────────────────────

    def _candidate_dir(self, candidate_id: str) -> Path:
        return self._root / candidate_id

    def _interviews_dir(self, candidate_id: str) -> Path:
        return self._candidate_dir(candidate_id) / "interviews"

    def _interviews_index_path(self, candidate_id: str) -> Path:
        return self._interviews_dir(candidate_id) / "index.md"

    def _interview_dir(self, candidate_id: str, interview_id: str) -> Path:
        return self._interviews_dir(candidate_id) / interview_id

    def _session_json_path(self, candidate_id: str, interview_id: str) -> Path:
        return self._interview_dir(candidate_id, interview_id) / "session.json"

    def _transcript_path(self, candidate_id: str, interview_id: str) -> Path:
        return self._interview_dir(candidate_id, interview_id) / "transcript.md"

    def _rounds_wal_path(self, candidate_id: str, interview_id: str) -> Path:
        """rounds.jsonl: 面试进行中的 WAL，每完成一轮 append 一行。
        finish_interview 时归档为 rounds.jsonl.archived。"""
        return self._interview_dir(candidate_id, interview_id) / "rounds.jsonl"

    def _eval_report_path(self, candidate_id: str, interview_id: str) -> Path:
        return self._interview_dir(candidate_id, interview_id) / "eval_report.md"

    # ─── 面试 index 读写 ──────────────────────────────────────────────

    def _read_interviews_index(self, candidate_id: str) -> list[dict]:
        path = self._interviews_index_path(candidate_id)
        if not path.exists():
            return []
        try:
            text = path.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(text)
            return meta.get("interviews") or []
        except Exception:
            logger.exception("Failed to read interviews index for %s", candidate_id)
            return []

    def _write_interviews_index(
        self, candidate_id: str, interviews: list[dict]
    ) -> None:
        profile = self._candidates.read_profile_meta(candidate_id)
        candidate_name = profile.get("name", candidate_id) if profile else candidate_id
        path = self._interviews_index_path(candidate_id)
        _write_atomic(path, _build_interviews_index(candidate_name, interviews))

    # ─── 候选人 CRUD ─────────────────────────────────────────────────

    async def save_candidate(
        self, profile: CandidateProfile, resume_markdown: str
    ) -> str:
        return await self._candidates.save_candidate(profile, resume_markdown)

    async def get_candidate(self, candidate_id: str) -> CandidateProfile | None:
        return await self._candidates.get_candidate(candidate_id)

    async def get_resume_markdown(self, candidate_id: str) -> str:
        """返回 profile.md 的正文 Markdown（frontmatter 之后的部分）。"""
        return await self._candidates.get_resume_markdown(candidate_id)

    async def get_candidate_by_name(self, name: str) -> CandidateProfile | None:
        return await self._candidates.get_candidate_by_name(name)

    async def search_candidates(
        self, keyword: str = "", limit: int = 20, offset: int = 0
    ) -> list[CandidateProfile]:
        return await self._candidates.search_candidates(keyword, limit, offset)

    async def count_candidates(self, keyword: str = "") -> int:
        """返回符合关键词筛选的候选人总数（不受 limit/offset 影响）。"""
        return await self._candidates.count_candidates(keyword)

    async def delete_candidate(self, candidate_id: str) -> None:
        await self._candidates.delete_candidate(candidate_id)

    # ─── 候选人历史 ───────────────────────────────────────────────────

    async def get_candidate_history(
        self, candidate_id: str, limit: int = 3
    ) -> CandidateHistory | None:
        meta = self._candidates.read_profile_meta(candidate_id)
        if meta is None:
            return None
        interviews = self._read_interviews_index(candidate_id)
        if not interviews:
            return None

        summaries: list[InterviewSummary] = []
        for iv in interviews[:limit]:
            start_dt = _parse_dt(iv.get("start_time")) or datetime.now()
            score = iv.get("overall_score")
            overall_score: float | None = float(score) if score is not None else None
            summaries.append(
                InterviewSummary(
                    interview_id=iv["interview_id"],
                    date=start_dt,
                    overall_score=overall_score,
                    recommendation=iv.get("recommendation"),
                    key_findings=iv.get("key_findings") or "",
                )
            )

        candidate_name = meta.get("name", candidate_id)
        text = _format_history_summary(candidate_name, summaries)
        return CandidateHistory(past_interviews=summaries, history_summary=text)

    # ─── 面试生命周期 ─────────────────────────────────────────────────

    async def start_interview(self, session: InterviewSession) -> None:
        """面试开始：写 session.json（stage=interviewing）。"""
        candidate_id = session.candidate.id
        interview_id = session.id
        iv_dir = self._interview_dir(candidate_id, interview_id)
        iv_dir.mkdir(parents=True, exist_ok=True)

        session_data = {
            "interview_id": interview_id,
            "candidate_id": candidate_id,
            "start_time": session.metadata.start_time.isoformat(),
            "end_time": None,
            "stage": "interviewing",
            "trigger_mode": session.metadata.trigger_mode,
            "recording_candidate_path": "",
            "recording_interviewer_path": "",
            "context_summary": "",
        }
        _write_atomic(
            self._session_json_path(candidate_id, interview_id),
            json.dumps(session_data, ensure_ascii=False, indent=2),
        )

        logger.info(
            "start_interview written session_id=%s candidate_id=%s",
            interview_id,
            candidate_id,
        )

    async def append_round(
        self,
        candidate_id: str,
        interview_id: str,
        round_: ConversationRound,
    ) -> None:
        """每完成一轮后 append 到 `rounds.jsonl`（WAL），防止进程崩溃丢失。

        采用 append-only 写入；finish_interview 时会把该文件归档为 .archived。
        """
        path = self._rounds_wal_path(candidate_id, interview_id)
        record = {
            "round_number": round_.round_number,
            "interviewer_text": round_.interviewer_text,
            "candidate_text": round_.candidate_text,
            "timestamp": (
                getattr(round_, "timestamp", datetime.now()).isoformat()
                if not isinstance(getattr(round_, "timestamp", None), str)
                else round_.timestamp
            ),
        }
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"

        def _append() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()

        await asyncio.to_thread(_append)
        logger.debug(
            "append_round wal session_id=%s round=%d", interview_id, round_.round_number
        )

    async def scan_orphan_wal(self) -> list[dict]:
        """扫描所有候选人目录下未归档的 rounds.jsonl —— 上次进程崩溃前未 finish 的面试。

        每条返回 dict 含：candidate_id / candidate_name / interview_id / round_count /
        start_time / wal_path（绝对路径）。供 recovery API 列出供用户选择恢复或丢弃。
        """
        orphans: list[dict] = []
        if not self._root.exists():
            return orphans
        for cand_dir in self._root.iterdir():
            if not cand_dir.is_dir():
                continue
            interviews_dir = cand_dir / "interviews"
            if not interviews_dir.is_dir():
                continue
            cand_meta = self._candidates.read_profile_meta(cand_dir.name) or {}
            for iv_dir in interviews_dir.iterdir():
                if not iv_dir.is_dir():
                    continue
                wal_path = iv_dir / "rounds.jsonl"
                if not wal_path.exists():
                    continue
                # S-6: 若 transcript.md 已存在，说明 finish_interview 写入成功但
                # 归档 WAL 前崩溃——WAL 是冗余残留，不应列为待恢复 orphan，
                # 以避免重复 recover 覆盖已完整的 transcript。
                if (iv_dir / "transcript.md").exists():
                    logger.debug(
                        "scan_orphan_wal: skip %s (transcript.md already exists)",
                        wal_path,
                    )
                    continue
                round_count = 0
                try:
                    with open(wal_path, encoding="utf-8") as f:
                        for line in f:
                            if line.strip():
                                round_count += 1
                except Exception:
                    logger.warning(
                        "scan_orphan_wal: failed to read %s", wal_path, exc_info=True
                    )
                    continue
                start_time = ""
                session_json = iv_dir / "session.json"
                if session_json.exists():
                    try:
                        start_time = json.loads(
                            session_json.read_text(encoding="utf-8")
                        ).get("start_time", "")
                    except Exception:
                        pass
                orphans.append(
                    {
                        "candidate_id": cand_dir.name,
                        "candidate_name": (
                            str(cand_meta.get("name", "")) if cand_meta else ""
                        ),
                        "interview_id": iv_dir.name,
                        "round_count": round_count,
                        "start_time": start_time,
                        "wal_path": str(wal_path),
                    }
                )
        return orphans

    async def recover_interview_from_wal(
        self, candidate_id: str, interview_id: str
    ) -> int:
        """从 rounds.jsonl 重建 ConversationRound 列表并写入 transcript.md + 归档 WAL。

        Returns: 恢复出的 round 数量。

        与 finish_interview 的区别：本方法不读 InterviewSession（崩溃后无法重建完整 session），
        直接基于 WAL + session.json 拼装最小可用的归档结果。
        """
        wal_path = self._rounds_wal_path(candidate_id, interview_id)
        if not wal_path.exists():
            raise StorageError(f"WAL 不存在：{wal_path}")

        rounds: list[ConversationRound] = []
        with open(wal_path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    logger.warning(
                        "recover_interview_from_wal: skip malformed line %d in %s",
                        line_no,
                        wal_path,
                    )
                    continue
                ts_raw = rec.get("timestamp", "")
                ts = _parse_dt(ts_raw) or datetime.now()
                rounds.append(
                    ConversationRound(
                        round_number=int(rec.get("round_number", line_no)),
                        interviewer_text=str(rec.get("interviewer_text", "")),
                        candidate_text=str(rec.get("candidate_text", "")),
                        timestamp=ts,
                    )
                )

        if not rounds:
            # 空 WAL：直接归档丢弃
            archived = wal_path.with_suffix(".jsonl.archived")
            try:
                wal_path.replace(archived)
            except OSError:
                pass
            return 0

        iv_dir = self._interview_dir(candidate_id, interview_id)
        session_json_path = iv_dir / "session.json"
        try:
            existing = json.loads(session_json_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {
                "interview_id": interview_id,
                "candidate_id": candidate_id,
                "start_time": rounds[0].timestamp.isoformat(),
                "trigger_mode": "auto",
            }

        end_time = rounds[-1].timestamp

        # 用一个最小 session 拼 transcript.md（复用已有渲染逻辑）
        candidate_meta = self._candidates.read_profile_meta(candidate_id) or {}
        candidate = CandidateProfile(
            id=candidate_id,
            name=str(candidate_meta.get("name", "")) if candidate_meta else "",
        )
        from ..models.session import InterviewStage, SessionMetadata

        meta = SessionMetadata(
            candidate_id=candidate_id,
            start_time=_parse_dt(existing.get("start_time")) or rounds[0].timestamp,
            end_time=end_time,
            trigger_mode=str(existing.get("trigger_mode", "auto")),
            recording_candidate_path=str(existing.get("recording_candidate_path", ""))
            or None,
            recording_interviewer_path=str(
                existing.get("recording_interviewer_path", "")
            )
            or None,
        )
        recovered_session = InterviewSession(
            id=interview_id,
            candidate=candidate,
            rounds=rounds,
            stage=InterviewStage.COMPLETED,
            context_summary=str(existing.get("context_summary", "")) or "",
            interview_brief="",
            metadata=meta,
        )

        # 写 transcript.md + 更新 session.json + index + 归档 WAL（复用 finish_interview）
        await self.finish_interview(recovered_session)
        logger.info(
            "recover_interview_from_wal: recovered %d rounds candidate=%s interview=%s",
            len(rounds),
            candidate_id,
            interview_id,
        )
        return len(rounds)

    async def discard_orphan_wal(self, candidate_id: str, interview_id: str) -> bool:
        """丢弃残留 WAL：直接删除（已 finish_interview 的 .archived 不动）。"""
        wal_path = self._rounds_wal_path(candidate_id, interview_id)
        if not wal_path.exists():
            return False
        try:
            wal_path.unlink()
            logger.info(
                "discard_orphan_wal: deleted candidate=%s interview=%s",
                candidate_id,
                interview_id,
            )
            return True
        except OSError:
            logger.exception("discard_orphan_wal: failed to delete %s", wal_path)
            return False

    async def finish_interview(self, session: InterviewSession) -> None:
        """面试结束：写 transcript.md，更新 session.json，更新 index 文件。"""
        candidate_id = session.candidate.id
        interview_id = session.id
        end_time = session.metadata.end_time or datetime.now()

        # 1. 写 transcript.md
        _write_atomic(
            self._transcript_path(candidate_id, interview_id),
            _build_transcript_md(session),
        )

        # 2. 更新 session.json
        session_json_path = self._session_json_path(candidate_id, interview_id)
        try:
            existing = json.loads(session_json_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {
                "interview_id": interview_id,
                "candidate_id": candidate_id,
                "start_time": session.metadata.start_time.isoformat(),
                "trigger_mode": session.metadata.trigger_mode,
            }
        existing.update(
            {
                "end_time": end_time.isoformat(),
                "stage": "completed",
                "recording_candidate_path": session.metadata.recording_candidate_path
                or "",
                "recording_interviewer_path": session.metadata.recording_interviewer_path
                or "",
                "context_summary": session.context_summary or "",
            }
        )
        _write_atomic(
            session_json_path,
            json.dumps(existing, ensure_ascii=False, indent=2),
        )

        # 3. 更新 interviews/index.md
        interviews = self._read_interviews_index(candidate_id)
        iv_entry = {
            "interview_id": interview_id,
            "start_time": session.metadata.start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "stage": "completed",
            "trigger_mode": session.metadata.trigger_mode,
            "overall_score": None,
            "recommendation": None,
            "key_findings": "",
        }
        existing_idx = next(
            (
                i
                for i, iv in enumerate(interviews)
                if iv.get("interview_id") == interview_id
            ),
            -1,
        )
        if existing_idx >= 0:
            iv_entry["overall_score"] = interviews[existing_idx].get("overall_score")
            iv_entry["recommendation"] = interviews[existing_idx].get("recommendation")
            iv_entry["key_findings"] = interviews[existing_idx].get("key_findings", "")
            interviews[existing_idx] = iv_entry
        else:
            interviews.insert(0, iv_entry)
        self._write_interviews_index(candidate_id, interviews)

        # 4. 更新 candidates/index.md 中的 latest_interview
        self._candidates.touch_latest_interview(
            candidate_id, end_time.strftime("%Y-%m-%d")
        )

        # 5. 归档 rounds.jsonl WAL（transcript.md 已写入，WAL 完成使命）
        wal_path = self._rounds_wal_path(candidate_id, interview_id)
        if wal_path.exists():
            archived = wal_path.with_suffix(".jsonl.archived")
            try:
                wal_path.replace(archived)
                logger.debug(
                    "finish_interview archived WAL session_id=%s -> %s",
                    interview_id,
                    archived.name,
                )
            except OSError:
                logger.warning(
                    "finish_interview: failed to archive rounds.jsonl session_id=%s",
                    interview_id,
                    exc_info=True,
                )

        logger.info(
            "finish_interview done session_id=%s candidate_id=%s",
            interview_id,
            candidate_id,
        )

    # ─── 面试简报 ─────────────────────────────────────────────────────

    def save_brief(self, candidate_id: str, content: str) -> None:
        """原子写入候选人简报到 candidates/{id}/brief.md。"""
        self._candidates.save_brief(candidate_id, content)

    def get_brief(self, candidate_id: str) -> str:
        """读取候选人简报，文件不存在时返回空字符串。"""
        return self._candidates.get_brief(candidate_id)

    # ─── 结构化问题清单 ────────────────────────────────────────────────

    def save_questions(self, candidate_id: str, questions: list) -> None:
        """原子写入结构化问题清单（list[dict]）。"""
        self._candidates.save_questions(candidate_id, questions)

    def get_questions(self, candidate_id: str) -> list:
        """读取问题清单，不存在时返回空列表。"""
        return self._candidates.get_questions(candidate_id)

    def update_question_coverage(
        self,
        candidate_id: str,
        question_id: str,
        covered: bool,
        covered_by: str = "manual",
    ) -> bool:
        """更新单个问题的覆盖状态。返回是否找到该问题。"""
        return self._candidates.update_question_coverage(
            candidate_id, question_id, covered, covered_by
        )

    async def get_latest_eval_report(self, candidate_id: str) -> EvalReport | None:
        interviews = self._read_interviews_index(candidate_id)
        for iv in interviews:
            report = await self.get_eval_report(
                iv["interview_id"], candidate_id=candidate_id
            )
            if report is not None:
                return report
        return None

    # ─── 面试详情 ─────────────────────────────────────────────────────

    async def get_interview_detail(
        self, interview_id: str, candidate_id: str | None = None
    ) -> InterviewDetail | None:
        if candidate_id:
            iv_dir = self._interview_dir(candidate_id, interview_id)
            if not iv_dir.exists():
                return None
        else:
            # glob 扫描（单用户工具）
            matches = list(self._root.glob(f"*/interviews/{interview_id}"))
            if not matches:
                return None
            iv_dir = matches[0]
            candidate_id = iv_dir.parent.parent.name

        session_path = iv_dir / "session.json"
        if not session_path.exists():
            return None
        try:
            session_data = json.loads(session_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        # 解析 transcript.md 中的 rounds
        rounds: list[ConversationRound] = []
        transcript_path = iv_dir / "transcript.md"
        if transcript_path.exists():
            rounds = _parse_transcript(transcript_path.read_text(encoding="utf-8"))

        # 读取 eval report
        eval_report = await self.get_eval_report(
            interview_id, candidate_id=candidate_id
        )

        rec_candidate = session_data.get("recording_candidate_path", "")
        rec_interviewer = session_data.get("recording_interviewer_path", "")
        recording_paths: RecordingPaths | None = None
        if rec_candidate or rec_interviewer:
            recording_paths = RecordingPaths(
                full_candidate=rec_candidate,
                full_interviewer=rec_interviewer,
            )

        return InterviewDetail(
            interview_id=interview_id,
            candidate_id=candidate_id,
            start_time=_parse_dt(session_data.get("start_time")) or datetime.now(),
            end_time=_parse_dt(session_data.get("end_time")),
            rounds=rounds,
            eval_report=eval_report,
            recording_paths=recording_paths,
        )

    # ─── 评价报告 ─────────────────────────────────────────────────────

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
        profile_meta = self._candidates.read_profile_meta(candidate_id)
        candidate_name = profile_meta.get("name", "") if profile_meta else ""
        path = self._eval_report_path(candidate_id, report.interview_id)
        _write_atomic(path, _build_eval_report_md(report, candidate_name))

        # 更新 interviews/index.md 中的评分和关键结论
        interviews = self._read_interviews_index(candidate_id)
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
                _sj = self._session_json_path(candidate_id, report.interview_id)
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
        self._write_interviews_index(candidate_id, interviews)

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

    # ─── 内部工具 ─────────────────────────────────────────────────────

    async def _find_candidate_for_interview(self, interview_id: str) -> str | None:
        """扫描 candidates/*/interviews/{interview_id} 找到对应的 candidate_id。"""
        matches = list(self._root.glob(f"*/interviews/{interview_id}"))
        if matches:
            return matches[0].parent.parent.name
        return None

    async def rebuild_index(self) -> None:
        """从目录结构重建 candidates/index.md 和各 interviews/index.md。"""
        candidates: list[dict] = []
        for cand_dir in sorted(self._root.iterdir()):
            if not cand_dir.is_dir() or cand_dir.name == ".":
                continue
            profile_path = cand_dir / "profile.md"
            if not profile_path.exists():
                continue
            try:
                text = profile_path.read_text(encoding="utf-8")
                meta, _ = _parse_frontmatter(text)
            except Exception:
                continue
            candidate_id = meta.get("id") or cand_dir.name
            name = meta.get("name") or candidate_id
            created_at = str(meta.get("created_at") or "")[:10]

            # 重建 interviews/index.md
            interviews_dir = cand_dir / "interviews"
            iv_entries: list[dict] = []
            if interviews_dir.exists():
                for iv_dir in sorted(interviews_dir.iterdir(), reverse=True):
                    if not iv_dir.is_dir():
                        continue
                    session_json = iv_dir / "session.json"
                    if not session_json.exists():
                        continue
                    try:
                        sd = json.loads(session_json.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    # 读 eval_report 填充评分和关键结论
                    eval_path = iv_dir / "eval_report.md"
                    overall_score = None
                    recommendation = None
                    key_findings = ""
                    if eval_path.exists():
                        try:
                            emeta, _ = _parse_frontmatter(
                                eval_path.read_text(encoding="utf-8")
                            )
                            overall_score = emeta.get("overall_score")
                            recommendation = emeta.get("recommendation")
                            kf_parts = []
                            if emeta.get("strengths"):
                                kf_parts.append(
                                    "优势: " + "; ".join(list(emeta["strengths"])[:2])
                                )
                            if emeta.get("weaknesses"):
                                kf_parts.append(
                                    "不足: " + "; ".join(list(emeta["weaknesses"])[:2])
                                )
                            key_findings = "，".join(kf_parts)
                        except Exception:
                            pass
                    iv_entries.append(
                        {
                            "interview_id": iv_dir.name,
                            "start_time": sd.get("start_time", ""),
                            "end_time": sd.get("end_time"),
                            "stage": sd.get("stage", "completed"),
                            "trigger_mode": sd.get("trigger_mode", "auto"),
                            "overall_score": overall_score,
                            "recommendation": recommendation,
                            "key_findings": key_findings,
                        }
                    )
            if iv_entries:
                _write_atomic(
                    interviews_dir / "index.md",
                    _build_interviews_index(name, iv_entries),
                )

            latest_interview = None
            if iv_entries:
                latest_end = (
                    iv_entries[0].get("end_time")
                    or iv_entries[0].get("start_time")
                    or ""
                )
                latest_interview = latest_end[:10] if latest_end else None

            candidates.append(
                {
                    "id": candidate_id,
                    "name": name,
                    "created_at": created_at,
                    "latest_interview": latest_interview,
                }
            )

        self._candidates._write_candidates_index(candidates)
        logger.info("rebuild_index done: %d candidates", len(candidates))
