"""Baidu real-time speech recognition WebSocket client (async)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import websockets

logger = logging.getLogger(__name__)

BAIDU_WSS_URL = "wss://vop.baidu.com/realtime_asr"
BAIDU_CHUNK_BYTES = 5120  # 160ms at 16kHz 16-bit mono


@dataclass
class TranscriptSegment:
    text: str
    source: str                # "candidate" | "interviewer" | "mixed"
    is_final: bool             # MID_TEXT=False, FIN_TEXT=True
    start_time: Optional[int]  # ms, only present in FIN_TEXT
    end_time: Optional[int]    # ms, only present in FIN_TEXT
    timestamp: float


class BaiduRealtimeSTT:
    """Baidu real-time ASR WebSocket client.

    Lifecycle::

        stt = BaiduRealtimeSTT(appid, appkey, dev_pid, "candidate")
        await stt.connect()

        # Concurrently:
        await stt.send_audio(pcm_chunk)
        async for segment in stt.receive_loop():
            ...

        await stt.close()
    """

    def __init__(
        self,
        appid: int,
        appkey: str,
        dev_pid: int,
        source_label: str,
        cuid: Optional[str] = None,
    ) -> None:
        self._appid = appid
        self._appkey = appkey
        self._dev_pid = dev_pid
        self._source_label = source_label
        self._cuid = cuid or uuid.uuid4().hex
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._sn: str = ""

    async def connect(self) -> None:
        """Establish WebSocket connection and send START frame."""
        self._sn = str(uuid.uuid4())
        url = f"{BAIDU_WSS_URL}?sn={self._sn}"
        self._ws = await websockets.connect(url)
        start_frame = {
            "type": "START",
            "data": {
                "appid": self._appid,
                "appkey": self._appkey,
                "dev_pid": self._dev_pid,
                "cuid": self._cuid,
                "format": "pcm",
                "sample": 16000,
            },
        }
        await self._ws.send(json.dumps(start_frame))
        logger.info(
            "BaiduRealtimeSTT connected (source=%s, sn=%s)",
            self._source_label,
            self._sn,
        )

    async def send_audio(self, pcm_chunk: bytes) -> None:
        """Send a binary PCM audio chunk."""
        if self._ws is None:
            raise RuntimeError("Not connected; call connect() first")
        await self._ws.send(pcm_chunk)

    async def receive_loop(self) -> AsyncIterator[TranscriptSegment]:
        """Receive recognition results and yield TranscriptSegment.

        Stops when the WebSocket connection is closed.
        """
        if self._ws is None:
            raise RuntimeError("Not connected; call connect() first")

        async for message in self._ws:
            if isinstance(message, bytes):
                continue
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                logger.warning(
                    "Unparseable message (source=%s): %.200s",
                    self._source_label,
                    message,
                )
                continue

            msg_type = data.get("type")
            err_no = data.get("err_no", 0)

            if msg_type == "MID_TEXT":
                text = data.get("result", "")
                if text:
                    yield TranscriptSegment(
                        text=text,
                        source=self._source_label,
                        is_final=False,
                        start_time=None,
                        end_time=None,
                        timestamp=time.time(),
                    )
            elif msg_type == "FIN_TEXT":
                if err_no != 0:
                    logger.error(
                        "Baidu ASR error (source=%s): err_no=%s err_msg=%s",
                        self._source_label,
                        err_no,
                        data.get("err_msg", ""),
                    )
                else:
                    text = data.get("result", "")
                    if text:
                        yield TranscriptSegment(
                            text=text,
                            source=self._source_label,
                            is_final=True,
                            start_time=data.get("start_time"),
                            end_time=data.get("end_time"),
                            timestamp=time.time(),
                        )
            elif msg_type == "HEARTBEAT":
                logger.debug("Heartbeat received (source=%s)", self._source_label)
            else:
                logger.debug(
                    "Received type=%s (source=%s)",
                    msg_type,
                    self._source_label,
                )

    async def close(self) -> None:
        """Send FINISH frame and close the WebSocket connection."""
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "FINISH"}))
                await self._ws.close()
            except Exception:
                logger.debug(
                    "Error during close (source=%s)",
                    self._source_label,
                    exc_info=True,
                )
            finally:
                self._ws = None
        logger.info("BaiduRealtimeSTT closed (source=%s)", self._source_label)
