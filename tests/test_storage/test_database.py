"""Tests for src/storage/database.py."""
from __future__ import annotations

import pytest

from src.models.exceptions import StorageError
from src.storage.database import Database


@pytest.mark.asyncio
async def test_initialize_creates_all_tables() -> None:
    db = Database(":memory:")
    await db.initialize()
    try:
        async with db.connection() as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cursor:
                rows = await cursor.fetchall()
        names = {r["name"] for r in rows}
    finally:
        await db.close()

    assert {"Candidate", "Interview", "ConversationRound", "EvalReport", "TokenUsage"} <= names


@pytest.mark.asyncio
async def test_initialize_creates_indexes() -> None:
    db = Database(":memory:")
    await db.initialize()
    try:
        async with db.connection() as conn:
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ) as cursor:
                rows = await cursor.fetchall()
        names = {r["name"] for r in rows}
    finally:
        await db.close()

    assert "idx_candidate_name" in names
    assert "idx_interview_candidate" in names
    assert "idx_round_interview" in names
    assert "idx_token_interview" in names


@pytest.mark.asyncio
async def test_connection_before_initialize_raises() -> None:
    db = Database(":memory:")
    with pytest.raises(StorageError):
        async with db.connection() as _:
            pass


@pytest.mark.asyncio
async def test_initialize_is_idempotent() -> None:
    db = Database(":memory:")
    await db.initialize()
    await db.initialize()
    try:
        async with db.connection() as conn:
            async with conn.execute("SELECT 1") as cursor:
                row = await cursor.fetchone()
        assert row is not None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_close_releases_connection() -> None:
    db = Database(":memory:")
    await db.initialize()
    await db.close()
    with pytest.raises(StorageError):
        async with db.connection() as _:
            pass


@pytest.mark.asyncio
async def test_close_without_initialize_is_safe() -> None:
    db = Database(":memory:")
    await db.close()