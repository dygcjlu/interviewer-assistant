"""火山引擎豆包大模型实时 ASR WebSocket 客户端。

协议参考：https://www.volcengine.com/docs/6561/80816
连接端点：wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
鉴权方式：HTTP 请求头 X-Api-App-Key / X-Api-Access-Key / X-Api-Resource-Id
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import uuid
from datetime import datetime
from typing import AsyncIterator

from websockets.asyncio.client import connect as ws_connect

from ..config import get_settings
from .protocol import TranscriptSegment

logger = logging.getLogger(__name__)

_WSS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
_SAMPLE_RATE = 16000
_CHANNELS = 1
_BITS = 16
_SEND_CHUNK_BYTES = 6400       # 200ms × 16000Hz × 2bytes
_RECONNECT_DELAY_SEC = 1.5


# ── Binary frame helpers ───────────────────────────────────────────────────────

def _build_full_client_request(payload_json: bytes) -> bytes:
    """Build a Full Client Request binary frame (no compression).

    Structure:
      [0x11, 0x10, 0x10, 0x00]  4-byte header
      [4B big-endian payload size]
      [N bytes JSON payload]
    """
    header = bytes([0x11, 0x10, 0x10, 0x00])
    return header + struct.pack(">I", len(payload_json)) + payload_json


def _build_audio_frame(audio: bytes, seq: int, is_last: bool) -> bytes:
    """Build an Audio Only Request binary frame (no compression).

    Normal frame  → flags=0x01 (positive sequence)
    Last frame    → flags=0x03 (negative sequence)

    Structure:
      [0x11, type_flags, 0x00, 0x00]  4-byte header
      [4B signed big-endian: ±seq]
      [4B big-endian: payload size]
      [N bytes raw PCM]
    """
    flags = 0x23 if is_last else 0x21
    signed_seq = -seq if is_last else seq
    header = bytes([0x11, flags, 0x00, 0x00])
    return (
        header
        + struct.pack(">i", signed_seq)
        + struct.pack(">I", len(audio))
        + audio
    )


def _parse_server_response(data: bytes) -> dict | None:
    """Parse a Full Server Response binary frame.

    Returns a dict with an 'utterances' list on success, or None on error/malformed input.
    Error frames (msg_type=0x0F) are logged and return None.
    """
    try:
        if len(data) < 4:
            return None

        msg_type = (data[1] >> 4) & 0x0F
        flags = data[1] & 0x0F

        if msg_type == 0x0F:
            # Error frame: [4B error_code][4B msg_size][N bytes msg]
            if len(data) >= 12:
                error_code = struct.unpack(">I", data[4:8])[0]
                msg_size = struct.unpack(">I", data[8:12])[0]
                error_msg = data[12:12 + msg_size].decode(errors="replace")
                logger.warning("VolcASR server error: code=%d msg=%s", error_code, error_msg)
            return None

        offset = 4
        if flags & 0x01:
            offset += 4  # skip sequence field

        if len(data) < offset + 4:
            return None

        payload_size = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4
        payload_bytes = data[offset:offset + payload_size]
        parsed = json.loads(payload_bytes)

        result = parsed.get("result", {})
        utterances = result.get("utterances")
        if not utterances:
            return None

        return {"utterances": utterances}

    except Exception:
        logger.debug("VolcASR: failed to parse server response", exc_info=True)
        return None
