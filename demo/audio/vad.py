"""Voice Activity Detection (VAD) filter using webrtcvad.

webrtcvad requires frames of exactly 10, 20, or 30 ms at 8000/16000/32000/48000 Hz.
This module slices larger AudioFrames (e.g. 300 ms) into 30 ms sub-frames,
runs VAD on each, and decides whether the overall frame contains speech.
"""

from __future__ import annotations

import logging

import numpy as np
import webrtcvad

from demo.audio.capture import AudioFrame, SAMPLE_RATE

logger = logging.getLogger(__name__)

VAD_SUB_FRAME_MS = 30
VAD_SUB_FRAME_SAMPLES = int(SAMPLE_RATE * VAD_SUB_FRAME_MS / 1000)


class VADFilter:
    """Wraps webrtcvad to classify AudioFrames as speech or silence.

    Parameters
    ----------
    aggressiveness : int
        webrtcvad aggressiveness mode (0-3). Higher values filter more
        aggressively, i.e. require stronger speech signal to return True.
    speech_threshold : float
        Fraction of sub-frames that must be speech for the whole frame
        to be considered speech.  E.g. 0.3 means >= 30% sub-frames are speech.
    """

    def __init__(self, aggressiveness: int = 2, speech_threshold: float = 0.3) -> None:
        if not 0 <= aggressiveness <= 3:
            raise ValueError("aggressiveness must be 0-3")
        self._vad = webrtcvad.Vad(aggressiveness)
        self._speech_threshold = speech_threshold

    def is_speech(self, frame: AudioFrame) -> bool:
        """Return True if the frame contains speech above the threshold."""
        pcm = self._to_int16(frame.data)
        total_sub = 0
        speech_sub = 0
        offset = 0
        sub_bytes = VAD_SUB_FRAME_SAMPLES * 2  # 16-bit = 2 bytes per sample

        while offset + sub_bytes <= len(pcm):
            chunk = pcm[offset : offset + sub_bytes]
            total_sub += 1
            if self._vad.is_speech(chunk, SAMPLE_RATE):
                speech_sub += 1
            offset += sub_bytes

        if total_sub == 0:
            return False
        ratio = speech_sub / total_sub
        return ratio >= self._speech_threshold

    def filter(self, frame: AudioFrame) -> AudioFrame | None:
        """Return the frame if it contains speech, otherwise None."""
        if self.is_speech(frame):
            return frame
        return None

    @staticmethod
    def _to_int16(data: np.ndarray) -> bytes:
        clamped = np.clip(data, -1.0, 1.0)
        return (clamped * 32767).astype(np.int16).tobytes()
