"""Repository 层：基础 CRUD，返回 dict 或 list[dict]。

由 :class:`MemoryModule` 负责 dict → dataclass 的转换；
本层不感知业务数据结构。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..models.evaluation import EvalReport
from ..models.session import ConversationRound

from .database import Database

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CandidateRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(
        self,
        id: str,
        name: str,
        resume_text: str,
        profile_json: str,
    ) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO Candidate
                    (id, name, resume_text, profile_json, created_at)
                VALUES (?, ?, ?, ?,
                    COALESCE((SELECT created_at FROM Candidate WHERE id = ?), ?))
                """,
                (id, name, resume_text, profile_json, id, _utcnow_iso()),
            )
            await conn.commit()

    async def get_by_id(self, id: str) -> dict | None:
        async with self._db.connection() as conn:
            async with conn.execute(
                "SELECT * FROM Candidate WHERE id = ?", (id,)
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def search_by_name(self, keyword: str, limit: int = 20) -> list[dict]:
        pattern = f"%{keyword}%" if keyword else "%"
        async with self._db.connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM Candidate
                WHERE name LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (pattern, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_profile(self, id: str, profile_json: str) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE Candidate SET profile_json = ? WHERE id = ?",
                (profile_json, id),
            )
            await conn.commit()

    async def delete(self, id: str) -> None:
        async with self._db.connection() as conn:
            await conn.execute("DELETE FROM Candidate WHERE id = ?", (id,))
            await conn.commit()

    async def get_by_name_exact(self, name: str) -> dict | None:
        async with self._db.connection() as conn:
            async with conn.execute(
                "SELECT * FROM Candidate WHERE name = ? ORDER BY created_at DESC LIMIT 1",
                (name,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None


class InterviewRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(
        self,
        id: str,
        candidate_id: str,
        start_time: datetime,
        question_plan_json: str,
        trigger_mode: str,
    ) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO Interview
                    (id, candidate_id, start_time, end_time, stage,
                     question_plan_json, context_summary, trigger_mode,
                     full_recording_candidate_path, full_recording_interviewer_path)
                VALUES (?, ?, ?,
                    (SELECT end_time FROM Interview WHERE id = ?),
                    COALESCE((SELECT stage FROM Interview WHERE id = ?), 'idle'),
                    ?,
                    COALESCE((SELECT context_summary FROM Interview WHERE id = ?), ''),
                    ?,
                    (SELECT full_recording_candidate_path FROM Interview WHERE id = ?),
                    (SELECT full_recording_interviewer_path FROM Interview WHERE id = ?))
                """,
                (
                    id,
                    candidate_id,
                    start_time.isoformat(),
                    id,
                    id,
                    question_plan_json,
                    id,
                    trigger_mode,
                    id,
                    id,
                ),
            )
            await conn.commit()

    async def get_by_id(self, id: str) -> dict | None:
        async with self._db.connection() as conn:
            async with conn.execute(
                "SELECT * FROM Interview WHERE id = ?", (id,)
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def get_by_candidate(
        self, candidate_id: str, limit: int = 10
    ) -> list[dict]:
        async with self._db.connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM Interview
                WHERE candidate_id = ?
                ORDER BY start_time DESC
                LIMIT ?
                """,
                (candidate_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def delete_by_candidate(self, candidate_id: str) -> list[str]:
        """级联删除候选人所有面试记录及子记录（ConversationRound / EvalReport / TokenUsage），
        返回被删除的 interview_id 列表。"""
        async with self._db.connection() as conn:
            async with conn.execute(
                "SELECT id FROM Interview WHERE candidate_id = ?", (candidate_id,)
            ) as cursor:
                rows = await cursor.fetchall()
            interview_ids = [row[0] for row in rows]
            if interview_ids:
                placeholders = ",".join("?" * len(interview_ids))
                ids_tuple = tuple(interview_ids)
                # 先删除子表记录，再删除 Interview（遵从 FK 约束顺序）
                await conn.execute(
                    f"DELETE FROM ConversationRound WHERE interview_id IN ({placeholders})",
                    ids_tuple,
                )
                await conn.execute(
                    f"DELETE FROM EvalReport WHERE interview_id IN ({placeholders})",
                    ids_tuple,
                )
                await conn.execute(
                    f"DELETE FROM TokenUsage WHERE interview_id IN ({placeholders})",
                    ids_tuple,
                )
                await conn.execute(
                    "DELETE FROM Interview WHERE candidate_id = ?", (candidate_id,)
                )
            await conn.commit()
        return interview_ids

    async def update_on_finish(
        self,
        id: str,
        end_time: datetime,
        context_summary: str,
        recording_candidate_path: str,
        recording_interviewer_path: str,
    ) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                """
                UPDATE Interview
                SET end_time = ?,
                    context_summary = ?,
                    full_recording_candidate_path = ?,
                    full_recording_interviewer_path = ?,
                    stage = 'completed'
                WHERE id = ?
                """,
                (
                    end_time.isoformat(),
                    context_summary,
                    recording_candidate_path,
                    recording_interviewer_path,
                    id,
                ),
            )
            await conn.commit()


class RoundRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, interview_id: str, round: ConversationRound) -> int:
        timestamp = (
            round.timestamp.isoformat()
            if isinstance(round.timestamp, datetime)
            else str(round.timestamp)
        )
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO ConversationRound
                    (interview_id, round_number, interviewer_text, candidate_text,
                     llm_suggestion, candidate_audio_path, interviewer_audio_path,
                     timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    interview_id,
                    round.round_number,
                    round.interviewer_text,
                    round.candidate_text,
                    round.llm_suggestion,
                    round.candidate_audio_path,
                    round.interviewer_audio_path,
                    timestamp,
                ),
            )
            row_id = cursor.lastrowid
            await cursor.close()
            await conn.commit()
        assert row_id is not None
        return int(row_id)

    async def get_by_interview(self, interview_id: str) -> list[dict]:
        async with self._db.connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM ConversationRound
                WHERE interview_id = ?
                ORDER BY round_number ASC, id ASC
                """,
                (interview_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def delete_by_interview(self, interview_id: str) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                "DELETE FROM ConversationRound WHERE interview_id = ?",
                (interview_id,),
            )
            await conn.commit()


class EvalReportRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, report: EvalReport) -> None:
        import json

        scores_payload = {
            "dimensions": [
                {
                    "dimension": d.dimension,
                    "score": d.score,
                    "comment": d.comment,
                    "evidence": d.evidence,
                }
                for d in report.dimensions
            ],
            "overall_score": report.overall_score,
            "generated_at": report.generated_at.isoformat(),
        }
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO EvalReport
                    (id, interview_id, scores_json, strengths, weaknesses,
                     recommendation, full_report)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.id,
                    report.interview_id,
                    json.dumps(scores_payload, ensure_ascii=False),
                    json.dumps(report.strengths, ensure_ascii=False),
                    json.dumps(report.weaknesses, ensure_ascii=False),
                    report.recommendation,
                    report.summary,
                ),
            )
            await conn.commit()

    async def get_by_interview(self, interview_id: str) -> dict | None:
        async with self._db.connection() as conn:
            async with conn.execute(
                "SELECT * FROM EvalReport WHERE interview_id = ?",
                (interview_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def delete_by_interview_ids(self, interview_ids: list[str]) -> None:
        if not interview_ids:
            return
        placeholders = ",".join("?" * len(interview_ids))
        async with self._db.connection() as conn:
            await conn.execute(
                f"DELETE FROM EvalReport WHERE interview_id IN ({placeholders})",
                interview_ids,
            )
            await conn.commit()


class TokenUsageRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(
        self,
        interview_id: str,
        round_number: int,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO TokenUsage
                    (interview_id, round_number, prompt_tokens,
                     completion_tokens, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    interview_id,
                    round_number,
                    prompt_tokens,
                    completion_tokens,
                    _utcnow_iso(),
                ),
            )
            await conn.commit()

    async def get_total_by_interview(
        self, interview_id: str
    ) -> tuple[int, int]:
        async with self._db.connection() as conn:
            async with conn.execute(
                """
                SELECT
                    COALESCE(SUM(prompt_tokens), 0),
                    COALESCE(SUM(completion_tokens), 0)
                FROM TokenUsage
                WHERE interview_id = ?
                """,
                (interview_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return (0, 0)
        return (int(row[0] or 0), int(row[1] or 0))