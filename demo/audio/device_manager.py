"""Audio device management: enumeration, selection, and hot-plug detection."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import soundcard as sc

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 3.0


@dataclass(frozen=True)
class DeviceInfo:
    """Serialisable snapshot of an audio device."""

    id: str
    name: str
    is_loopback: bool = False

    @staticmethod
    def from_sc_device(dev: sc._Speaker | sc._Microphone) -> DeviceInfo:
        return DeviceInfo(
            id=str(dev.id),
            name=dev.name,
            is_loopback=getattr(dev, "isloopback", False),
        )


class AudioDeviceManager:
    """Enumerate audio devices and detect hot-plug changes.

    Provides helpers to:
      - List all speakers, microphones, and loopback devices.
      - Resolve a device by id or name substring.
      - Register a callback that fires when the device list changes.
    """

    def __init__(self) -> None:
        self._on_change: Callable[[list[DeviceInfo], list[DeviceInfo]], None] | None = None
        self._poll_thread: threading.Thread | None = None
        self._polling = False
        self._last_mic_ids: set[str] = set()
        self._last_speaker_ids: set[str] = set()

    # ---- enumeration ----

    @staticmethod
    def list_microphones(include_loopback: bool = False) -> list[DeviceInfo]:
        return [
            DeviceInfo.from_sc_device(m)
            for m in sc.all_microphones(include_loopback=include_loopback)
        ]

    @staticmethod
    def list_speakers() -> list[DeviceInfo]:
        return [DeviceInfo.from_sc_device(s) for s in sc.all_speakers()]

    @staticmethod
    def list_loopback_devices() -> list[DeviceInfo]:
        return [
            DeviceInfo.from_sc_device(m)
            for m in sc.all_microphones(include_loopback=True)
            if getattr(m, "isloopback", False)
        ]

    # ---- selection helpers ----

    @staticmethod
    def get_microphone(id_or_name: str) -> sc._Microphone:
        """Get a microphone by id string or name substring."""
        return sc.get_microphone(id_or_name, include_loopback=False)

    @staticmethod
    def get_loopback(id_or_name: str) -> sc._Microphone:
        """Get a loopback microphone by id string or name substring."""
        return sc.get_microphone(id_or_name, include_loopback=True)

    @staticmethod
    def get_speaker(id_or_name: str) -> sc._Speaker:
        return sc.get_speaker(id_or_name)

    @staticmethod
    def default_microphone() -> DeviceInfo:
        return DeviceInfo.from_sc_device(sc.default_microphone())

    @staticmethod
    def default_speaker() -> DeviceInfo:
        return DeviceInfo.from_sc_device(sc.default_speaker())

    # ---- hot-plug detection ----

    def set_on_change(
        self,
        callback: Callable[[list[DeviceInfo], list[DeviceInfo]], None],
    ) -> None:
        """Register a callback ``(mics, speakers) -> None`` fired on device list changes."""
        self._on_change = callback

    def start_monitoring(self, interval: float = DEFAULT_POLL_INTERVAL) -> None:
        """Start background polling for device changes."""
        if self._polling:
            return
        self._polling = True
        self._snapshot_device_ids()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            args=(interval,),
            daemon=True,
        )
        self._poll_thread.start()
        logger.info("Device monitoring started (interval=%.1fs)", interval)

    def stop_monitoring(self) -> None:
        self._polling = False
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None
        logger.info("Device monitoring stopped")

    def _snapshot_device_ids(self) -> None:
        self._last_mic_ids = {str(m.id) for m in sc.all_microphones(include_loopback=True)}
        self._last_speaker_ids = {str(s.id) for s in sc.all_speakers()}

    def _poll_loop(self, interval: float) -> None:
        while self._polling:
            time.sleep(interval)
            if not self._polling:
                break
            try:
                cur_mic_ids = {str(m.id) for m in sc.all_microphones(include_loopback=True)}
                cur_speaker_ids = {str(s.id) for s in sc.all_speakers()}
                if cur_mic_ids != self._last_mic_ids or cur_speaker_ids != self._last_speaker_ids:
                    self._last_mic_ids = cur_mic_ids
                    self._last_speaker_ids = cur_speaker_ids
                    logger.info("Audio device change detected")
                    if self._on_change:
                        mics = self.list_microphones(include_loopback=True)
                        speakers = self.list_speakers()
                        self._on_change(mics, speakers)
            except Exception:
                logger.exception("Error during device polling")
