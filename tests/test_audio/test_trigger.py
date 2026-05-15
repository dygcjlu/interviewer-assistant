"""Tests for SuggestionTrigger."""
import asyncio
import pytest
from unittest.mock import AsyncMock
from datetime import datetime

from src.audio.trigger import SuggestionTrigger
from src.audio.protocol import TranscriptSegment


def _make_segment(is_final: bool = True, source: str = "candidate") -> TranscriptSegment:
    return TranscriptSegment(
        text="test",
        source=source,
        is_final=is_final,
        timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_auto_trigger_fires_after_silence() -> None:
    triggered_ids: list[int] = []

    async def on_trigger(request_id: int) -> None:
        triggered_ids.append(request_id)

    trigger = SuggestionTrigger(on_trigger=on_trigger, silence_threshold_sec=0.05, min_interval_sec=0.0)
    trigger.on_candidate_segment(_make_segment(is_final=True))
    await asyncio.sleep(0.15)
    assert len(triggered_ids) == 1
    assert triggered_ids[0] == 0
    trigger.stop()


@pytest.mark.asyncio
async def test_cancel_pending_prevents_trigger() -> None:
    triggered_ids: list[int] = []

    async def on_trigger(request_id: int) -> None:
        triggered_ids.append(request_id)

    trigger = SuggestionTrigger(on_trigger=on_trigger, silence_threshold_sec=0.1, min_interval_sec=0.0)
    trigger.on_candidate_segment(_make_segment(is_final=True))
    trigger.cancel_pending()
    await asyncio.sleep(0.2)
    assert triggered_ids == []
    trigger.stop()


@pytest.mark.asyncio
async def test_manual_mode_does_not_auto_trigger() -> None:
    triggered_ids: list[int] = []

    async def on_trigger(request_id: int) -> None:
        triggered_ids.append(request_id)

    trigger = SuggestionTrigger(on_trigger=on_trigger, silence_threshold_sec=0.05, min_interval_sec=0.0)
    trigger.set_mode("manual")
    trigger.on_candidate_segment(_make_segment(is_final=True))
    await asyncio.sleep(0.15)
    assert triggered_ids == []
    trigger.stop()


@pytest.mark.asyncio
async def test_min_interval_suppresses_rapid_triggers() -> None:
    triggered_ids: list[int] = []

    async def on_trigger(request_id: int) -> None:
        triggered_ids.append(request_id)

    trigger = SuggestionTrigger(on_trigger=on_trigger, silence_threshold_sec=0.05, min_interval_sec=10.0)
    # First trigger
    trigger.on_candidate_segment(_make_segment(is_final=True))
    await asyncio.sleep(0.15)
    assert len(triggered_ids) == 1
    # Second trigger — should be suppressed by min_interval
    trigger.on_candidate_segment(_make_segment(is_final=True))
    await asyncio.sleep(0.15)
    assert len(triggered_ids) == 1  # still 1
    trigger.stop()


@pytest.mark.asyncio
async def test_non_final_segment_does_not_trigger() -> None:
    triggered_ids: list[int] = []

    async def on_trigger(request_id: int) -> None:
        triggered_ids.append(request_id)

    trigger = SuggestionTrigger(on_trigger=on_trigger, silence_threshold_sec=0.05, min_interval_sec=0.0)
    trigger.on_candidate_segment(_make_segment(is_final=False))
    await asyncio.sleep(0.15)
    assert triggered_ids == []
    trigger.stop()


def test_next_request_id_starts_at_zero() -> None:
    trigger = SuggestionTrigger(on_trigger=AsyncMock(), silence_threshold_sec=1.0)
    assert trigger.next_request_id == 0


def test_mode_default_is_auto() -> None:
    trigger = SuggestionTrigger(on_trigger=AsyncMock())
    assert trigger.mode == "auto"


def test_set_mode_invalid_raises() -> None:
    trigger = SuggestionTrigger(on_trigger=AsyncMock())
    with pytest.raises(ValueError):
        trigger.set_mode("invalid")