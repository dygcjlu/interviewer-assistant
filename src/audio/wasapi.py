"""真实 WASAPI 音频采集器（Windows-only）。

- 候选人声道（candidate）：使用 pyaudiowpatch 的 WASAPI Loopback 捕获系统扬声器输出。
  原生采样率通常为 48kHz / 2ch，通过 numpy 降采样混声到 16kHz / 1ch 后推送。
- 面试官声道（interviewer）：使用 sounddevice 捕获默认麦克风输入（MME）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from .protocol import AudioFrame

logger = logging.getLogger(__name__)

_TARGET_RATE = 16000
_CHANNELS = 1
_DTYPE = "int16"
_BLOCKSIZE_MS = 20  # 每帧 20ms


class WasapiCapturer:
    """双声道音频采集器。

    - candidate：WASAPI loopback（pyaudiowpatch），捕获扬声器回放。
    - interviewer：sounddevice 默认麦克风输入。
    """

    def __init__(self) -> None:
        self._callback: Callable[[AudioFrame], None] | None = None
        self._running = False
        self._loopback_stream = None  # pyaudiowpatch stream
        self._pyaudio = None  # PyAudio instance, kept alive
        self._mic_stream: sd.RawInputStream | None = None

    def set_on_frame(self, callback: Callable[[AudioFrame], None]) -> None:
        self._callback = callback

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        # 1. WASAPI loopback for candidate
        loopback_device_name = self._start_loopback_stream()

        # 2. sounddevice mic for interviewer
        blocksize = int(_TARGET_RATE * _BLOCKSIZE_MS / 1000)  # 320 samples
        self._mic_stream = sd.RawInputStream(
            samplerate=_TARGET_RATE,
            blocksize=blocksize,
            device=None,
            channels=_CHANNELS,
            dtype=_DTYPE,
            callback=self._make_sd_callback("interviewer"),
        )
        self._mic_stream.start()

        logger.info(
            "WasapiCapturer: started loopback=%r mic=default",
            loopback_device_name or "UNAVAILABLE",
        )

    async def stop(self) -> None:
        self._running = False

        if self._loopback_stream is not None:
            try:
                self._loopback_stream.stop_stream()
                self._loopback_stream.close()
            except Exception:
                logger.debug("WasapiCapturer: loopback stream close error (ignored)")
            self._loopback_stream = None

        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
            self._pyaudio = None

        if self._mic_stream is not None:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None

        logger.info("WasapiCapturer: stopped")

    # ── internals ──────────────────────────────────────────────────────────────

    def _start_loopback_stream(self) -> str | None:
        """使用 pyaudiowpatch 打开默认输出设备的 WASAPI Loopback 流。

        返回设备名（成功）或 None（不可用）。
        """
        try:
            import pyaudiowpatch as pyaudio  # noqa: PLC0415
        except ImportError:
            logger.warning(
                "WasapiCapturer: pyaudiowpatch not installed; loopback unavailable"
            )
            return None

        try:
            p = pyaudio.PyAudio()
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_out_idx = wasapi_info["defaultOutputDevice"]
            default_out = p.get_device_info_by_index(default_out_idx)

            # 查找对应默认输出的 loopback 设备
            loopback_device = None
            for dev in p.get_loopback_device_info_generator():
                if default_out["name"] in dev["name"]:
                    loopback_device = dev
                    break

            if loopback_device is None:
                # 如果没找到匹配的，使用第一个可用的 loopback
                for dev in p.get_loopback_device_info_generator():
                    loopback_device = dev
                    break

            if loopback_device is None:
                logger.warning("WasapiCapturer: no WASAPI loopback device found")
                p.terminate()
                return None

            native_rate = int(loopback_device["defaultSampleRate"])
            native_channels = loopback_device["maxInputChannels"]
            blocksize = int(native_rate * _BLOCKSIZE_MS / 1000)

            logger.info(
                "WasapiCapturer: loopback device [%d] %r native_rate=%d channels=%d",
                loopback_device["index"],
                loopback_device["name"],
                native_rate,
                native_channels,
            )

            stream = p.open(
                format=pyaudio.paInt16,
                channels=native_channels,
                rate=native_rate,
                frames_per_buffer=blocksize,
                input=True,
                input_device_index=loopback_device["index"],
                stream_callback=self._make_loopback_callback(
                    native_rate, native_channels
                ),
            )
            self._pyaudio = p
            self._loopback_stream = stream
            stream.start_stream()
            return loopback_device["name"]

        except Exception:
            logger.exception("WasapiCapturer: loopback stream open failed")
            if self._pyaudio is not None:
                self._pyaudio.terminate()
                self._pyaudio = None
            self._loopback_stream = None
            return None

    def _make_loopback_callback(self, native_rate: int, native_channels: int):
        """返回 pyaudiowpatch 流回调，负责降采样 + 混声 + 推送帧。

        与 sounddevice 回调一样，直接调用 self._callback（即
        AudioManager._sync_frame_callback），后者已通过
        run_coroutine_threadsafe 保证线程安全。
        """
        resample_needed = native_rate != _TARGET_RATE

        def _cb(in_data, frame_count, time_info, status) -> tuple:
            import pyaudiowpatch as pyaudio  # noqa: PLC0415

            if not self._running or self._callback is None:
                return (None, pyaudio.paContinue)

            arr = np.frombuffer(in_data, dtype=np.int16)

            # 多声道混声为单声道
            if native_channels > 1:
                arr = arr.reshape(-1, native_channels).mean(axis=1).astype(np.int16)

            # 降采样：native_rate → 16000
            if resample_needed:
                n_out = max(1, round(len(arr) * _TARGET_RATE / native_rate))
                indices = np.round(np.linspace(0, len(arr) - 1, n_out)).astype(np.intp)
                arr = arr[indices]

            frame = AudioFrame(
                data=arr.tobytes(),
                source="candidate",
                timestamp=time.monotonic(),
            )
            self._callback(frame)

            return (None, pyaudio.paContinue)

        return _cb

    def _make_sd_callback(self, source: str):
        """sounddevice 麦克风回调，通过 run_coroutine_threadsafe 传回主 loop。"""

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
            self._callback(frame)

        return _cb


__all__ = ["WasapiCapturer"]
