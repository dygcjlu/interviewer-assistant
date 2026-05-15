"""SQLite 连接与表结构管理。

维持一个长连接，所有 Repository 通过 :meth:`Database.connection` 获取受锁
保护的连接。使用 ``aiosqlite`` 提供异步访问；外键约束在初始化时启用。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

from ..models.exceptions import StorageError

logger = logging.getLogger(__name__)


_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS Candidate (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        resume_text TEXT NOT NULL DEFAULT '',
        profile_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS Interview (
        id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL REFERENCES Candidate(id),
        start_time TEXT NOT NULL,
        end_time TEXT,
        stage TEXT NOT NULL DEFAULT 'idle',
        question_plan_json TEXT NOT NULL DEFAULT '[]',
        context_summary TEXT NOT NULL DEFAULT '',
        trigger_mode TEXT NOT NULL DEFAULT 'auto',
        full_recording_candidate_path TEXT,
        full_recording_interviewer_path TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ConversationRound (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        interview_id TEXT NOT NULL REFERENCES Interview(id),
        round_number INTEGER NOT NULL,
        interviewer_text TEXT NOT NULL DEFAULT '',
        candidate_text TEXT NOT NULL DEFAULT '',
        llm_suggestion TEXT,
        candidate_audio_path TEXT,
        interviewer_audio_path TEXT,
        timestamp TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS EvalReport (
        id TEXT PRIMARY KEY,
        interview_id TEXT NOT NULL REFERENCES Interview(id),
        scores_json TEXT NOT NULL DEFAULT '[]',
        strengths TEXT NOT NULL DEFAULT '[]',
        weaknesses TEXT NOT NULL DEFAULT '[]',
        recommendation TEXT,
        full_report TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS TokenUsage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        interview_id TEXT NOT NULL REFERENCES Interview(id),
        round_number INTEGER,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        timestamp TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_candidate_name ON Candidate(name)",
    "CREATE INDEX IF NOT EXISTS idx_interview_candidate ON Interview(candidate_id, start_time)",
    "CREATE INDEX IF NOT EXISTS idx_round_interview ON ConversationRound(interview_id, round_number)",
    "CREATE INDEX IF NOT EXISTS idx_token_interview ON TokenUsage(interview_id)",
)


class Database:
    """SQLite 异步连接的薄封装。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._conn is not None:
            return
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        for stmt in _SCHEMA_STATEMENTS:
            await conn.execute(stmt)
        await conn.commit()
        self._conn = conn
        logger.info("Database initialized at %s", self._db_path)

    async def close(self) -> None:
        if self._conn is None:
            return
        try:
            await self._conn.close()
        except Exception:
            logger.exception("Failed to close database connection")
        finally:
            self._conn = None

    def connection(self) -> "_ConnectionContext":
        return _ConnectionContext(self)

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[aiosqlite.Connection]:
        if self._conn is None:
            raise StorageError("Database is not initialized; call initialize() first")
        async with self._lock:
            yield self._conn


class _ConnectionContext:
    """异步上下文管理器，串行化对持久连接的访问。"""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._cm: AsyncIterator[aiosqlite.Connection] | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        self._cm = self._db._acquire()
        return await self._cm.__aenter__()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        assert self._cm is not None
        await self._cm.__aexit__(exc_type, exc, tb)
        self._cm = None