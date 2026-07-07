"""InterviewStore：面试生命周期 + WAL（write-ahead log）持久化。

目录布局（相对 `root/{candidate_id}/interviews/`）：
  index.md                          # 本候选人的面试历史摘要
  {interview_id}/
  ├── transcript.md                 # 完整对话记录
  ├── session.json                  # 会话元数据
  └── rounds.jsonl                  # 面试进行中的 WAL，finish 后归档为 .archived
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from ..models.candidate import CandidateProfile
from ..models.exceptions import StorageError
from ..models.session import ConversationRound, InterviewSession
from ..utils import write_atomic as _write_atomic
from ._store_common import (
    InterviewDetail,
    RecordingPaths,
    _build_interviews_index,
    _build_transcript_md,
    _parse_dt,
    _parse_frontmatter,
    _parse_transcript,
)
from .candidate_store import CandidateStore

logger = logging.getLogger(__name__)


class InterviewStore:
    """基于文件系统的面试生命周期管理（含 WAL 崩溃恢复）。"""

    def __init__(self, root: Path, candidate_store: CandidateStore) -> None:
        self._root = root
        self._candidate_store = candidate_store

    # ─── 内部路径工具 ─────────────────────────────────────────────────

    def _candidate_dir(self, candidate_id: str) -> Path:
        return self._root / candidate_id

    def _interviews_dir(self, candidate_id: str) -> Path:
        return self._candidate_dir(candidate_id) / "interviews"

    def _interviews_index_path(self, candidate_id: str) -> Path:
        return self._interviews_dir(candidate_id) / "index.md"

    def interview_dir(self, candidate_id: str, interview_id: str) -> Path:
        return self._interviews_dir(candidate_id) / interview_id

    def session_json_path(self, candidate_id: str, interview_id: str) -> Path:
        return self.interview_dir(candidate_id, interview_id) / "session.json"

    def _transcript_path(self, candidate_id: str, interview_id: str) -> Path:
        return self.interview_dir(candidate_id, interview_id) / "transcript.md"

    def _rounds_wal_path(self, candidate_id: str, interview_id: str) -> Path:
        """rounds.jsonl: 面试进行中的 WAL，每完成一轮 append 一行。
        finish_interview 时归档为 rounds.jsonl.archived。"""
        return self.interview_dir(candidate_id, interview_id) / "rounds.jsonl"

    # ─── 面试 index 读写 ──────────────────────────────────────────────

    def read_interviews_index(self, candidate_id: str) -> list[dict]:
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

    def write_interviews_index(
        self, candidate_id: str, interviews: list[dict]
    ) -> None:
        profile = self._candidate_store.read_profile_meta(candidate_id)
        candidate_name = profile.get("name", candidate_id) if profile else candidate_id
        path = self._interviews_index_path(candidate_id)
        _write_atomic(path, _build_interviews_index(candidate_name, interviews))

    # ─── 面试生命周期 ─────────────────────────────────────────────────

    async def start_interview(self, session: InterviewSession) -> None:
        """面试开始：写 session.json（stage=interviewing）。"""
        candidate_id = session.candidate.id
        interview_id = session.id
        iv_dir = self.interview_dir(candidate_id, interview_id)
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
            self.session_json_path(candidate_id, interview_id),
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
            cand_meta = self._candidate_store.read_profile_meta(cand_dir.name) or {}
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

        iv_dir = self.interview_dir(candidate_id, interview_id)
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
        candidate_meta = self._candidate_store.read_profile_meta(candidate_id) or {}
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
        session_json_path = self.session_json_path(candidate_id, interview_id)
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
        interviews = self.read_interviews_index(candidate_id)
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
        self.write_interviews_index(candidate_id, interviews)

        # 4. 更新 candidates/index.md 中的 latest_interview
        self._candidate_store.touch_latest_interview(
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

    # ─── 面试详情（不含 eval_report，由 Facade 组装）───────────────────

    async def get_interview_detail_without_eval(
        self, interview_id: str, candidate_id: str | None = None
    ) -> InterviewDetail | None:
        """返回 InterviewDetail，`eval_report` 字段固定为 None。

        eval_report 属于 EvalStore 的职责；避免 InterviewStore 反向依赖
        EvalStore（否则与 EvalStore 依赖 InterviewStore 形成循环），
        由 Facade（`MemoryModule.get_interview_detail`）在拿到本方法结果后
        再调用 `EvalStore.get_eval_report` 填入。
        """
        if candidate_id:
            iv_dir = self.interview_dir(candidate_id, interview_id)
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
            eval_report=None,
            recording_paths=recording_paths,
        )
