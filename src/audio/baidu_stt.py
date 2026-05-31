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


_RECONNECT_DELAY_SEC = 1.5   # 断连后等待重连的秒数
_SEND_CHUNK_BYTES = 5120     # 百度建议每次发送 5120 字节（160ms）

# 致命错误码：服务端会关闭连接，需要重连
# -3005（本句无有效语音）是非致命错误，连接仍活跃，不应触发重连
_FATAL_ERR_NOS = {4002, -3004}

# 百度 ASR 会将上一句的末尾标点延迟到下一句结果的开头，需在接收层纠正
_LEADING_PUNCT = frozenset('，。！？；：、…"\'""\'\'（）【】「」')


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
        self._audio_buf: bytes = b""   # 累积缓冲，攒满 _SEND_CHUNK_BYTES 再发
        self._send_count: int = 0
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
                    "dev_pid": 15372,       # 普通话（与 demo 一致）
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

        累积到 _SEND_CHUNK_BYTES 字节后再整块发送（百度建议 5120 字节/次）。
        若连接已断开（如百度后端超时），自动触发后台重连，缓冲区清空。
        """
        if not self._connected or self._ws is None:
            self._audio_buf = b""  # 断连时清空缓冲，避免旧音频混入下次连接
            # 触发重连（不等待，丢弃本帧）
            if not self._closed and not self._reconnecting and self._app_id:
                asyncio.create_task(self._reconnect())
            return

        self._audio_buf += audio_data

        if len(self._audio_buf) < _SEND_CHUNK_BYTES:
            return  # 缓冲不够，继续积累

        chunk = self._audio_buf[:_SEND_CHUNK_BYTES]
        self._audio_buf = self._audio_buf[_SEND_CHUNK_BYTES:]

        self._send_count += 1
        if self._send_count == 1:
            import numpy as np  # noqa: PLC0415
            arr = np.frombuffer(chunk, dtype=np.int16)
            rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
            logger.debug(
                "BaiduRealtimeSTT [%s]: first_chunk bytes=%d rms=%.1f",
                self._channel, len(chunk), rms,
            )
        try:
            await self._ws.send(chunk)
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
                # 发送缓冲区剩余数据
                if self._audio_buf:
                    await self._ws.send(self._audio_buf)
                    self._audio_buf = b""
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
        """后台 task：持续从 WS 接收百度 ASR 响应，解析后入队。

        百度 ASR 存在已知行为：上一句的末尾标点会出现在下一句结果文本的开头。
        此处通过缓冲上一个 FIN_TEXT，在下一条结果到来时将开头的标点归还给上一句，
        再将修正后的 FIN_TEXT 入队，确保每句话首尾标点位置正确。
        """
        pending_fin_text: str | None = None  # 已识别但尚未入队的上一句 FIN_TEXT 文本

        async def _flush_pending(extra_punct: str = "") -> None:
            nonlocal pending_fin_text
            if pending_fin_text is None:
                return
            text = pending_fin_text + extra_punct
            await self._recv_queue.put(TranscriptSegment(
                text=text,
                source=self._channel,
                is_final=True,
                timestamp=datetime.now(),
            ))
            pending_fin_text = None

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
                    if err_no in _FATAL_ERR_NOS:
                        # 致命错误（如 4002 后端超时），服务端会关闭连接，标记断连退出
                        self._connected = False
                        break
                    # -3005（本句无有效语音）等非致命错误：连接仍活跃，继续接收下一句
                    continue

                result = msg.get("result", "")
                if not result:
                    continue

                is_final = msg_type == "FIN_TEXT"
                # 仅 MID_TEXT 和 FIN_TEXT 包含识别文本
                if msg_type not in ("MID_TEXT", "FIN_TEXT"):
                    continue

                # 将开头的孤立标点归还给上一句 FIN_TEXT
                leading_punct = ""
                while result and result[0] in _LEADING_PUNCT:
                    leading_punct += result[0]
                    result = result[1:]

                if leading_punct:
                    await _flush_pending(extra_punct=leading_punct)
                else:
                    await _flush_pending()

                if not result:
                    # 本条结果仅含标点，已合并到上一句，无需再入队
                    continue

                if is_final:
                    # 缓冲 FIN_TEXT，等待下一条结果确认其末尾标点已到位
                    pending_fin_text = result
                else:
                    await self._recv_queue.put(TranscriptSegment(
                        text=result,
                        source=self._channel,
                        is_final=False,
                        timestamp=datetime.now(),
                    ))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("BaiduRealtimeSTT [%s]: recv loop error", self._channel)
        finally:
            await _flush_pending()
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
