"""
完成标准：
- MockAudioCapturer 可以 start/stop 不报错
- start 后 is_running=True，stop 后 is_running=False
- 回调收到 AudioFrame，source="mixed"，data 为静音字节
- MockSTTEngine connect/close 不报错
"""
import asyncio
import pytest
from src.audio.mock import MockAudioCapturer, MockSTTEngine
from src.audio.protocol import AudioFrame


@pytest.mark.asyncio
async def test_mock_capturer_lifecycle():
    capturer = MockAudioCapturer()
    assert not capturer.is_running

    frames: list[AudioFrame] = []
    capturer.set_on_frame(frames.append)

    await capturer.start()
    assert capturer.is_running

    await asyncio.sleep(0.05)  # 等待约 2 帧
    await capturer.stop()
    assert not capturer.is_running
    assert len(frames) >= 1
    assert frames[0].source == "mixed"
    assert frames[0].data == b"\x00" * 320


@pytest.mark.asyncio
async def test_mock_stt_engine():
    stt = MockSTTEngine()
    await stt.connect()
    await stt.send_audio(b"\x00" * 100)
    await stt.close()
