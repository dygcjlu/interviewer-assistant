"""真实 WASAPI 音频采集器（Windows-only）。

使用 sounddevice 库：
  - loopback 设备 → 采集扬声器输出（候选人声音）→ source="candidate"
  - 默认麦克风    → 采集面试官声音         → source="interviewer"

两路音频帧均推入 AudioStreamBridge。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

import sounddevice as sd

from .protocol import AudioFrame

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_CHANNELS = 1
_DTYPE = "int16"
_BLOCKSIZE = 320          # 20ms @ 16kHz × 1ch × 2 bytes = 320 bytes


class WasapiCapturer:
    """双声道 WASAPI 音频采集器。

    - 候选人（loopback）：采集扬声器回放，source="candidate"
    - 面试官（mic）：采集麦克风输入，source="interviewer"
    """

    def __init__(self) -> None:
        self._callback: Callable[[AudioFrame], None] | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loopback_stream: sd.RawInputStream | None = None
        self._mic_stream: sd.RawInputStream | None = None

    def set_on_frame(self, callback: Callable[[AudioFrame], None]) -> None:
        self._callback = callback

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._running = True

        loopback_device = self._find_loopback_device()
        mic_device = self._find_mic_device()

        self._loopback_stream = self._open_input_stream(
            loopback_device, "candidate", fallback_to_default=True
        )
        self._mic_stream = sd.RawInputStream(
            samplerate=_SAMPLE_RATE,
            blocksize=_BLOCKSIZE,
            device=mic_device,
            channels=_CHANNELS,
            dtype=_DTYPE,
            callback=self._make_sd_callback("interviewer"),
        )

        self._loopback_stream.start()
        self._mic_stream.start()
        logger.info(
            "WasapiCapturer: started loopback_device=%s mic_device=%s",
            loopback_device,
            mic_device,
        )

    async def stop(self) -> None:
        self._running = False
        for stream in (self._loopback_stream, self._mic_stream):
            if stream is not None:
                stream.stop()
                stream.close()
        self._loopback_stream = None
        self._mic_stream = None
        logger.info("WasapiCapturer: stopped")

    # ── internals ──────────────────────────────────────────────────────────────

    def _open_input_stream(
        self, device: int | None, source: str, fallback_to_default: bool = False
    ) -> sd.RawInputStream:
        """Open a RawInputStream, falling back to device=None if the given device fails."""
        if device is not None and fallback_to_default:
            try:
                return sd.RawInputStream(
                    samplerate=_SAMPLE_RATE,
                    blocksize=_BLOCKSIZE,
                    device=device,
                    channels=_CHANNELS,
                    dtype=_DTYPE,
                    callback=self._make_sd_callback(source),
                )
            except Exception as exc:
                logger.warning(
                    "WasapiCapturer: device %s failed (%s), falling back to default input",
                    device,
                    exc,
                )
                device = None
        return sd.RawInputStream(
            samplerate=_SAMPLE_RATE,
            blocksize=_BLOCKSIZE,
            device=device,
            channels=_CHANNELS,
            dtype=_DTYPE,
            callback=self._make_sd_callback(source),
        )

    def _make_sd_callback(self, source: str):
        """返回 sounddevice 回调，在采集线程调用，通过 run_coroutine_threadsafe 传回主 loop。"""
        def _cb(indata: bytes, frames: int, time_info, status) -> None:
            if status:
                logger.debug("WasapiCapturer [%s] status: %s", source, status)
            if not self._running or self._callback is None:
                return
            frame = AudioFrame(
                data=bytes(indata),
                source=source,
                timestamp=time.monotonic(),
            )
            self._callback(frame)          # AudioManager._sync_frame_callback 封装了线程安全
        return _cb

    @staticmethod
    def _find_loopback_device() -> int | None:
        """查找 WASAPI loopback 设备（扬声器回放）。找不到时返回 None（sounddevice 使用默认设备）。"""
        try:
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                name: str = dev.get("name", "").lower()
                if dev.get("max_input_channels", 0) > 0 and (
                    "loopback" in name or "立体声混音" in name or "stereo mix" in name
                ):
                    logger.info("WasapiCapturer: loopback device [%d] %s", i, dev["name"])
                    return i
        except Exception:
            logger.exception("WasapiCapturer: loopback device query failed")
        logger.warning("WasapiCapturer: no loopback device found, using default input")
        return None

    @staticmethod
    def _find_mic_device() -> int | None:
        """返回系统默认麦克风（None = sounddevice 默认输入）。"""
        return None


__all__ = ["WasapiCapturer"]
