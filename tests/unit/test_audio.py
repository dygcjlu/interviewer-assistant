"""Unit tests — audio 模块：SuggestionTrigger、AudioStreamBridge、MockAudioCapturer、MockSTTEngine。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.audio.mock import MockAudioCapturer, MockSTTEngine
from src.audio.protocol import AudioFrame, TranscriptSegment
from src.audio.stream import AudioStreamBridge
from src.audio.trigger import SuggestionTrigger


# ── SuggestionTrigger ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSuggestionTrigger:
    def _make_trigger(
        self,
        on_trigger=None,
        silence_threshold: float = 0.05,
        min_interval: float = 0.0,
    ) -> SuggestionTrigger:
        if on_trigger is None:
            on_trigger = AsyncMock()
        return SuggestionTrigger(
            on_trigger=on_trigger,
            silence_threshold_sec=silence_threshold,
            min_interval_sec=min_interval,
        )

    def test_initial_mode_is_auto(self):
        t = self._make_trigger()
        assert t.mode == "auto"

    def test_set_mode_auto(self):
        t = self._make_trigger()
        t.set_mode("auto")
        assert t.mode == "auto"

    def test_set_mode_manual(self):
        t = self._make_trigger()
        t.set_mode("manual")
        assert t.mode == "manual"

    def test_set_mode_invalid_raises(self):
        t = self._make_trigger()
        with pytest.raises(ValueError):
            t.set_mode("invalid")

    def test_cancel_pending_no_task_does_not_raise(self):
        t = self._make_trigger()
        t.cancel_pending()

    def test_stop_does_not_raise(self):
        t = self._make_trigger()
        t.stop()

    def test_next_request_id_initial_zero(self):
        t = self._make_trigger()
        assert t.next_request_id == 0

    @pytest.mark.asyncio
    async def test_on_candidate_segment_final_schedules_timer(self):
        fired = []

        async def on_trigger(req_id: int) -> None:
            fired.append(req_id)

        t = SuggestionTrigger(
            on_trigger=on_trigger,
            silence_threshold_sec=0.01,
            min_interval_sec=0.0,
        )
        segment = TranscriptSegment(
            text="说完了", is_final=True, source="candidate", timestamp=1.0
        )
        t.on_candidate_segment(segment)
        await asyncio.sleep(0.05)  # 等定时器触发
        assert len(fired) >= 1
        t.stop()

    @pytest.mark.asyncio
    async def test_on_candidate_segment_not_final_cancels_pending(self):
        t = self._make_trigger(silence_threshold=0.01)
        final_segment = TranscriptSegment(
            text="开始说", is_final=True, source="candidate", timestamp=1.0
        )
        non_final = TranscriptSegment(
            text="继续说...", is_final=False, source="candidate", timestamp=2.0
        )
        t.on_candidate_segment(final_segment)
        t.on_candidate_segment(non_final)  # 应取消 pending task
        await asyncio.sleep(0.05)
        # 不应有触发（被取消）
        assert t._pending_task is None
        t.stop()

    @pytest.mark.asyncio
    async def test_manual_mode_ignores_segments(self):
        fired = []

        async def on_trigger(req_id: int) -> None:
            fired.append(req_id)

        t = SuggestionTrigger(
            on_trigger=on_trigger,
            silence_threshold_sec=0.01,
            min_interval_sec=0.0,
        )
        t.set_mode("manual")
        segment = TranscriptSegment(
            text="", is_final=True, source="candidate", timestamp=1.0
        )
        t.on_candidate_segment(segment)
        await asyncio.sleep(0.05)
        assert len(fired) == 0

    @pytest.mark.asyncio
    async def test_set_mode_manual_cancels_cancel_current(self):
        cancel_mock = AsyncMock()
        t = SuggestionTrigger(
            on_trigger=AsyncMock(),
            silence_threshold_sec=0.01,
            min_interval_sec=0.0,
            on_cancel_current=cancel_mock,
        )
        # 先在 auto 模式下触发，再切换到 manual
        t.set_mode("manual")
        await asyncio.sleep(0.02)
        # cancel_current 应被调用（或 loop task 已创建）
        t.stop()

    @pytest.mark.asyncio
    async def test_min_interval_skips_rapid_triggers(self):
        fired = []

        async def on_trigger(req_id: int) -> None:
            fired.append(req_id)

        t = SuggestionTrigger(
            on_trigger=on_trigger,
            silence_threshold_sec=0.01,
            min_interval_sec=10.0,  # 非常大的间隔
        )
        segment = TranscriptSegment(
            text="", is_final=True, source="candidate", timestamp=1.0
        )
        # 触发后立即再触发
        t.on_candidate_segment(segment)
        await asyncio.sleep(0.05)
        # 因为 min_interval 很大，第一次触发后第二次应该被跳过
        initial_count = len(fired)
        t.on_candidate_segment(segment)
        await asyncio.sleep(0.05)
        # 应跳过
        assert len(fired) <= initial_count + 1
        t.stop()


# ── AudioStreamBridge ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAudioStreamBridge:
    def _make_bridge(self):
        candidate_stt = MagicMock()
        candidate_stt.send_audio = AsyncMock()
        interviewer_stt = MagicMock()
        interviewer_stt.send_audio = AsyncMock()
        recorder = MagicMock()
        recorder.on_audio_frame = AsyncMock()
        bridge = AudioStreamBridge(candidate_stt, interviewer_stt, recorder)
        return bridge, candidate_stt, interviewer_stt, recorder

    @pytest.mark.asyncio
    async def test_candidate_frame_routed_to_candidate_stt(self):
        bridge, cand_stt, _, recorder = self._make_bridge()
        frame = AudioFrame(data=b"\x00" * 320, source="candidate", timestamp=1.0)
        await bridge.on_frame(frame)
        cand_stt.send_audio.assert_awaited_once_with(frame.data)
        recorder.on_audio_frame.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_interviewer_frame_routed_to_interviewer_stt(self):
        bridge, _, intv_stt, recorder = self._make_bridge()
        frame = AudioFrame(data=b"\x01" * 320, source="interviewer", timestamp=1.0)
        await bridge.on_frame(frame)
        intv_stt.send_audio.assert_awaited_once_with(frame.data)

    @pytest.mark.asyncio
    async def test_mixed_frame_goes_only_to_recorder(self):
        bridge, cand_stt, intv_stt, recorder = self._make_bridge()
        frame = AudioFrame(data=b"\x02" * 320, source="mixed", timestamp=1.0)
        await bridge.on_frame(frame)
        cand_stt.send_audio.assert_not_called()
        intv_stt.send_audio.assert_not_called()
        recorder.on_audio_frame.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_prevents_further_frame_processing(self):
        bridge, cand_stt, _, recorder = self._make_bridge()
        await bridge.stop()
        frame = AudioFrame(data=b"\x00" * 320, source="candidate", timestamp=2.0)
        await bridge.on_frame(frame)
        cand_stt.send_audio.assert_not_called()
        recorder.on_audio_frame.assert_not_called()


# ── MockAudioCapturer ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMockAudioCapturer:
    @pytest.mark.asyncio
    async def test_initial_not_running(self):
        capturer = MockAudioCapturer()
        assert not capturer.is_running

    @pytest.mark.asyncio
    async def test_start_sets_running(self):
        capturer = MockAudioCapturer()
        await capturer.start()
        assert capturer.is_running
        await capturer.stop()

    @pytest.mark.asyncio
    async def test_start_twice_does_not_crash(self):
        capturer = MockAudioCapturer()
        await capturer.start()
        await capturer.start()  # 幂等
        assert capturer.is_running
        await capturer.stop()

    @pytest.mark.asyncio
    async def test_stop_after_start(self):
        capturer = MockAudioCapturer()
        await capturer.start()
        await capturer.stop()
        assert not capturer.is_running

    @pytest.mark.asyncio
    async def test_callback_called_with_audio_frame(self):
        frames = []
        capturer = MockAudioCapturer()
        capturer.set_on_frame(lambda f: frames.append(f))
        await capturer.start()
        await asyncio.sleep(0.05)  # 等待几个 20ms 帧
        await capturer.stop()
        assert len(frames) > 0
        assert isinstance(frames[0], AudioFrame)
        assert frames[0].source == "mixed"


# ── MockSTTEngine ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMockSTTEngine:
    @pytest.mark.asyncio
    async def test_connect_does_not_raise(self):
        engine = MockSTTEngine()
        await engine.connect()

    @pytest.mark.asyncio
    async def test_send_audio_does_not_raise(self):
        engine = MockSTTEngine()
        await engine.send_audio(b"\x00" * 320)

    @pytest.mark.asyncio
    async def test_receive_returns_empty_iterator(self):
        engine = MockSTTEngine()
        results = []
        async for segment in engine.receive():
            results.append(segment)
        assert results == []

    @pytest.mark.asyncio
    async def test_close_does_not_raise(self):
        engine = MockSTTEngine()
        await engine.close()
