"""百度实时语音识别（Realtime ASR）WebSocket 客户端。

协议参考：https://ai.baidu.com/ai-doc/SPEECH/Wkh86eoho
连接端点：wss://vop.baidu.com/realtime_asr
鉴权方式：START 帧中直接携带 appid + appkey（实时 ASR 专用短期鉴权，无需 OAuth token）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import AsyncIterator

from websockets.asyncio.client import connect as ws_connect

from ..config import get_settings
from .protocol import TranscriptSegment

logger = logging.getLogger(__name__)

_WSS_URL = "wss://vop.baidu.com/realtime_asr"
_SAMPLE_RATE = 16000
_FORMAT = "pcm"


_RECONNECT_DELAY_SEC = 2.0   # 断连后等待重连的秒数


class BaiduRealtimeSTT:
    """百度实时 ASR WebSocket 客户端。

    每个实例对应一个声道（candidate 或 interviewer）。
    connect() 建立连接；send_audio() 推送 PCM 帧；receive() 异步迭代识别结果。
    凭据缺失时 connect() 静默返回，服务仍可正常运行（无识别输出）。
    断连（如百度后端超时 err_no=4002）后自动重连。
    """

    def __init__(self, channel: str = "candidate") -> None:
        self._channel = channel
        self._ws = None
        self._connected = False
        self._closed = False       # close() 调用后设为 True，禁止重连
        self._reconnecting = False # 防止并发重连
        self._recv_queue: asyncio.Queue[TranscriptSegment] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        settings = get_settings()
        self._app_id: str = getattr(settings, "BAIDU_APP_ID", "")
        self._api_key: str = getattr(settings, "BAIDU_API_KEY", "")

    # ── STTEngine protocol ─────────────────────────────────────────────────────

    async def connect(self) -> None:
        """建立 WebSocket 连接并发送开始帧。"""
        if not self._app_id or not self._api_key:
            logger.warning(
                "BaiduRealtimeSTT [%s]: BAIDU_APP_ID/BAIDU_API_KEY not configured, using mock mode",
                self._channel,
            )
            return

        sn = str(uuid.uuid4())
        url = f"{_WSS_URL}?sn={sn}"

        try:
            self._ws = await ws_connect(url)
            start_frame = {
                "type": "START",
                "data": {
                    "appid": int(self._app_id),
                    "appkey": self._api_key,
                    "dev_pid": 80001,       # 普通话，支持标点
                    "cuid": f"interviewer-assistant-{self._channel}",
                    "format": _FORMAT,
                    "sample": _SAMPLE_RATE,
                },
            }
            await self._ws.send(json.dumps(start_frame))
            self._connected = True
            self._recv_task = asyncio.create_task(self._recv_loop())
            logger.info("BaiduRealtimeSTT [%s]: connected sn=%s", self._channel, sn)
        except Exception:
            logger.exception("BaiduRealtimeSTT [%s]: connect failed", self._channel)
            self._ws = None

    async def send_audio(self, audio_data: bytes) -> None:
        """发送 PCM 音频帧到百度 ASR。

        若连接已断开（如百度后端超时），自动触发后台重连，本帧丢弃。
        """
        if not self._connected or self._ws is None:
            # 触发重连（不等待，丢弃本帧）
            if not self._closed and not self._reconnecting and self._app_id:
                asyncio.create_task(self._reconnect())
            return
        try:
            await self._ws.send(audio_data)
        except Exception:
            logger.debug("BaiduRealtimeSTT [%s]: send_audio error", self._channel)
            self._connected = False

    def receive(self) -> AsyncIterator[TranscriptSegment]:
        """返回识别结果的异步迭代器（从内部队列消费）。"""
        return self._queue_iter()

    async def close(self) -> None:
        """发送结束帧，关闭连接。"""
        self._closed = True   # 阻止 send_audio 触发重连
        if self._ws is not None and self._connected:
            try:
                await self._ws.send(json.dumps({"type": "FINISH"}))
                await self._ws.close()
            except Exception:
                logger.debug("BaiduRealtimeSTT [%s]: close error (ignored)", self._channel)
        self._connected = False
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        self._ws = None
        logger.info("BaiduRealtimeSTT [%s]: closed", self._channel)

    # ── internals ─────────────────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """后台 task：持续从 WS 接收百度 ASR 响应，解析后入队。"""
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")
                err_no = msg.get("err_no", 0)

                if err_no != 0:
                    logger.warning(
                        "BaiduRealtimeSTT [%s]: err_no=%d err_msg=%s",
                        self._channel,
                        err_no,
                        msg.get("err_msg", ""),
                    )
                    # 服务端返回错误后连接通常即将关闭，立即标记断连并退出
                    # 让 send_audio 在下次调用时立即触发重连，避免 ~10s 的延迟
                    self._connected = False
                    break

                result = msg.get("result", "")
                if not result:
                    continue

                is_final = msg_type == "FIN_TEXT"
                # 仅 MID_TEXT 和 FIN_TEXT 包含识别文本
                if msg_type not in ("MID_TEXT", "FIN_TEXT"):
                    continue

                segment = TranscriptSegment(
                    text=result,
                    source=self._channel,
                    is_final=is_final,
                    timestamp=datetime.now(),
                )
                await self._recv_queue.put(segment)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("BaiduRealtimeSTT [%s]: recv loop error", self._channel)
        finally:
            # WebSocket 被服务器关闭（如 backend timeout 后）时，标记为断连
            self._connected = False
            logger.debug("BaiduRealtimeSTT [%s]: recv loop exited, _connected set to False", self._channel)

    async def _reconnect(self) -> None:
        """断连后延迟重连，仅当未被 close() 且当前未在重连时执行。"""
        if self._reconnecting or self._closed:
            return
        self._reconnecting = True
        try:
            logger.info(
                "BaiduRealtimeSTT [%s]: reconnecting in %.1fs...",
                self._channel,
                _RECONNECT_DELAY_SEC,
            )
            await asyncio.sleep(_RECONNECT_DELAY_SEC)
            if self._closed:
                return
            # 取消旧 recv task（如果还在跑）
            if self._recv_task and not self._recv_task.done():
                self._recv_task.cancel()
                try:
                    await self._recv_task
                except asyncio.CancelledError:
                    pass
            self._ws = None
            await self.connect()
            logger.info("BaiduRealtimeSTT [%s]: reconnect %s", self._channel,
                        "ok" if self._connected else "failed")
        except Exception:
            logger.exception("BaiduRealtimeSTT [%s]: reconnect error", self._channel)
        finally:
            self._reconnecting = False

    async def _queue_iter(self) -> AsyncIterator[TranscriptSegment]:
        """从内部队列异步产出 TranscriptSegment。"""
        while True:
            segment = await self._recv_queue.get()
            yield segment


__all__ = ["BaiduRealtimeSTT"]
