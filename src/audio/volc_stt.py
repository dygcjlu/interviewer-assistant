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
from collections.abc import AsyncIterator
from datetime import datetime

from websockets.asyncio.client import connect as ws_connect

from ..config import get_settings
from .protocol import TranscriptSegment

logger = logging.getLogger(__name__)

_WSS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
_SAMPLE_RATE = 16000
_CHANNELS = 1
_BITS = 16
_SEND_CHUNK_BYTES = 6400  # 200ms × 16000Hz × 2bytes
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

    Normal frame  → byte1=0x21 (msg_type=0010, flags=0001 positive seq)
    Last frame    → byte1=0x23 (msg_type=0010, flags=0011 negative seq)

    Structure:
      [0x11, type_flags, 0x00, 0x00]  4-byte header
      [4B signed big-endian: ±seq]
      [4B big-endian: payload size]
      [N bytes raw PCM]
    """
    type_flags = 0x23 if is_last else 0x21
    signed_seq = -seq if is_last else seq
    header = bytes([0x11, type_flags, 0x00, 0x00])
    return (
        header + struct.pack(">i", signed_seq) + struct.pack(">I", len(audio)) + audio
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
                error_msg = data[12 : 12 + msg_size].decode(errors="replace")
                logger.warning(
                    "VolcASR server error: code=%d msg=%s", error_code, error_msg
                )
            return None

        offset = 4
        if flags & 0x01:
            offset += 4  # skip sequence field

        if len(data) < offset + 4:
            return None

        payload_size = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        payload_bytes = data[offset : offset + payload_size]
        parsed = json.loads(payload_bytes)

        result = parsed.get("result", {})
        utterances = result.get("utterances")
        if not utterances:
            return None

        return {"utterances": utterances}

    except Exception:
        logger.debug("VolcASR: failed to parse server response", exc_info=True)
        return None


class VolcRealtimeSTT:
    """火山引擎豆包大模型实时 ASR WebSocket 客户端。

    每个实例对应一个声道（candidate 或 interviewer）。
    connect() 建立连接；send_audio() 推送 PCM 帧；receive() 异步迭代识别结果。
    凭据缺失时 connect() 静默返回，服务仍可正常运行（无识别输出）。
    断连后自动重连。
    """

    def __init__(self, channel: str = "candidate") -> None:
        self._channel = channel
        self._ws = None
        self._connected = False
        self._closed = False
        self._reconnecting = False
        self._recv_queue: asyncio.Queue[TranscriptSegment] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        self._audio_buf: bytes = b""
        self._seq: int = 0
        settings = get_settings()
        self._app_key: str = settings.VOLC_APP_KEY
        self._access_key: str = settings.VOLC_ACCESS_KEY
        self._resource_id: str = settings.VOLC_RESOURCE_ID

    # ── STTEngine protocol ─────────────────────────────────────────────────────

    async def connect(self) -> None:
        """建立 WebSocket 连接，发送 Full Client Request。"""
        if not self._app_key or not self._access_key:
            logger.warning(
                "VolcRealtimeSTT [%s]: VOLC_APP_KEY/VOLC_ACCESS_KEY not configured, using mock mode",
                self._channel,
            )
            return

        headers = {
            "X-Api-App-Key": self._app_key,
            "X-Api-Access-Key": self._access_key,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

        try:
            self._ws = await ws_connect(_WSS_URL, additional_headers=headers)
            req_payload = json.dumps(
                {
                    "user": {"uid": f"interviewer-assistant-{self._channel}"},
                    "audio": {
                        "format": "pcm",
                        "sample_rate": _SAMPLE_RATE,
                        "bits": _BITS,
                        "channel": _CHANNELS,
                        "language": "zh-CN",
                    },
                    "request": {
                        "model_name": "bigmodel",
                        "show_utterances": True,
                        "result_type": "single",
                    },
                }
            ).encode()
            await self._ws.send(_build_full_client_request(req_payload))
            self._connected = True
            self._seq = 1  # FCR implicitly occupies seq=1; audio frames start at seq=2
            self._recv_task = asyncio.create_task(self._recv_loop())
            logger.info("VolcRealtimeSTT [%s]: connected", self._channel)
        except Exception:
            logger.exception("VolcRealtimeSTT [%s]: connect failed", self._channel)
            self._ws = None

    async def send_audio(self, audio_data: bytes) -> None:
        """发送 PCM 音频帧，累积 6400 字节后整块发送。"""
        if not self._connected or self._ws is None:
            self._audio_buf = b""
            if not self._closed and not self._reconnecting and self._app_key:
                asyncio.create_task(self._reconnect())
            return

        self._audio_buf += audio_data
        if len(self._audio_buf) < _SEND_CHUNK_BYTES:
            return

        chunk = self._audio_buf[:_SEND_CHUNK_BYTES]
        self._audio_buf = self._audio_buf[_SEND_CHUNK_BYTES:]
        self._seq += 1
        try:
            await self._ws.send(_build_audio_frame(chunk, seq=self._seq, is_last=False))
        except Exception:
            logger.debug("VolcRealtimeSTT [%s]: send_audio error", self._channel)
            self._connected = False

    def receive(self) -> AsyncIterator[TranscriptSegment]:
        """返回识别结果的异步迭代器（从内部队列消费）。"""
        return self._queue_iter()

    async def close(self) -> None:
        """发送剩余音频的负包帧，关闭连接。"""
        self._closed = True
        if self._ws is not None and self._connected:
            try:
                self._seq += 1
                await self._ws.send(
                    _build_audio_frame(self._audio_buf, seq=self._seq, is_last=True)
                )
                self._audio_buf = b""
                await self._ws.close()
            except Exception:
                logger.debug(
                    "VolcRealtimeSTT [%s]: close error (ignored)", self._channel
                )
        self._connected = False
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        self._ws = None
        logger.info("VolcRealtimeSTT [%s]: closed", self._channel)

    # ── internals ─────────────────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """后台 task：持续从 WS 接收响应，解析 utterances 后入队。"""
        try:
            async for raw in self._ws:
                if not isinstance(raw, bytes):
                    continue
                parsed = _parse_server_response(raw)
                if parsed is None:
                    continue
                for utterance in parsed.get("utterances", []):
                    text = utterance.get("text", "")
                    if not text:
                        continue
                    is_final = bool(utterance.get("definite", False))
                    await self._recv_queue.put(
                        TranscriptSegment(
                            text=text,
                            source=self._channel,
                            is_final=is_final,
                            timestamp=datetime.now(),
                        )
                    )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("VolcRealtimeSTT [%s]: recv loop error", self._channel)
        finally:
            self._connected = False
            logger.debug("VolcRealtimeSTT [%s]: recv loop exited", self._channel)

    async def _reconnect(self) -> None:
        """断连后延迟重连。"""
        if self._reconnecting or self._closed:
            return
        self._reconnecting = True
        try:
            logger.info(
                "VolcRealtimeSTT [%s]: reconnecting in %.1fs...",
                self._channel,
                _RECONNECT_DELAY_SEC,
            )
            await asyncio.sleep(_RECONNECT_DELAY_SEC)
            if self._closed:
                return
            if self._recv_task and not self._recv_task.done():
                self._recv_task.cancel()
                try:
                    await self._recv_task
                except asyncio.CancelledError:
                    pass
            self._ws = None
            await self.connect()
            logger.info(
                "VolcRealtimeSTT [%s]: reconnect %s",
                self._channel,
                "ok" if self._connected else "failed",
            )
        except Exception:
            logger.exception("VolcRealtimeSTT [%s]: reconnect error", self._channel)
        finally:
            self._reconnecting = False

    async def _queue_iter(self) -> AsyncIterator[TranscriptSegment]:
        """从内部队列异步产出 TranscriptSegment。"""
        while True:
            segment = await self._recv_queue.get()
            yield segment


__all__ = ["VolcRealtimeSTT"]
