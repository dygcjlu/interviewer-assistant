"""科大讯飞实时语音转写大模型 WebSocket 客户端。

协议参考：https://www.xfyun.cn/doc/spark/asr_llm/rtasr_llm.html
连接端点：wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1?{请求参数}
鉴权方式：URL 参数中携带 HMAC-SHA1 签名（握手前完成鉴权）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator
from urllib.parse import quote, urlencode

from websockets.asyncio.client import connect as ws_connect

from ..config import get_settings
from .protocol import TranscriptSegment

logger = logging.getLogger(__name__)

_WSS_BASE = "wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1"
_SAMPLE_RATE = 16000
_SEND_CHUNK_BYTES = 1280     # 官方建议：每 40ms 发送 1280 字节
_RECONNECT_DELAY_SEC = 1.5

# 讯飞 ASR 会将上一句的末尾标点延迟到下一句结果的开头，需在接收层纠正
_LEADING_PUNCT = frozenset("，。！？；：、…"'""''（）【】「」")


class XunfeiRealtimeSTT:
    """讯飞实时语音转写大模型 WebSocket 客户端。

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
        self._session_id: str | None = None     # 从握手响应 sid 字段获取
        self._recv_queue: asyncio.Queue[TranscriptSegment] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        self._audio_buf: bytes = b""
        settings = get_settings()
        self._app_id: str = getattr(settings, "XUNFEI_APP_ID", "")
        self._access_key_id: str = getattr(settings, "XUNFEI_ACCESS_KEY_ID", "")
        self._access_key_secret: str = getattr(settings, "XUNFEI_ACCESS_KEY_SECRET", "")

    # ── STTEngine protocol ─────────────────────────────────────────────────────

    async def connect(self) -> None:
        """建立 WebSocket 连接（鉴权签名嵌入 URL）。"""
        if not self._app_id or not self._access_key_id or not self._access_key_secret:
            logger.warning(
                "XunfeiRealtimeSTT [%s]: XUNFEI_APP_ID/ACCESS_KEY_ID/ACCESS_KEY_SECRET "
                "not configured, using mock mode",
                self._channel,
            )
            return

        url = self._build_url()
        try:
            self._ws = await ws_connect(url)
            self._connected = True
            self._recv_task = asyncio.create_task(self._recv_loop())
            logger.info("XunfeiRealtimeSTT [%s]: connected", self._channel)
        except Exception:
            logger.exception("XunfeiRealtimeSTT [%s]: connect failed", self._channel)
            self._ws = None

    async def send_audio(self, audio_data: bytes) -> None:
        """发送 PCM 音频帧到讯飞 ASR。

        累积到 _SEND_CHUNK_BYTES 字节后再整块发送（官方建议 1280 字节/40ms）。
        """
        if not self._connected or self._ws is None:
            self._audio_buf = b""
            if not self._closed and not self._reconnecting and self._app_id:
                asyncio.create_task(self._reconnect())
            return

        self._audio_buf += audio_data

        if len(self._audio_buf) < _SEND_CHUNK_BYTES:
            return

        chunk = self._audio_buf[:_SEND_CHUNK_BYTES]
        self._audio_buf = self._audio_buf[_SEND_CHUNK_BYTES:]

        try:
            await self._ws.send(chunk)
        except Exception:
            logger.debug("XunfeiRealtimeSTT [%s]: send_audio error", self._channel)
            self._connected = False

    def receive(self) -> AsyncIterator[TranscriptSegment]:
        """返回识别结果的异步迭代器（从内部队列消费）。"""
        return self._queue_iter()

    async def close(self) -> None:
        """发送结束帧，关闭连接。"""
        self._closed = True
        if self._ws is not None and self._connected:
            try:
                if self._audio_buf:
                    await self._ws.send(self._audio_buf)
                    self._audio_buf = b""
                end_frame = {"end": True, "sessionId": self._session_id or ""}
                await self._ws.send(json.dumps(end_frame))
                await self._ws.close()
            except Exception:
                logger.debug("XunfeiRealtimeSTT [%s]: close error (ignored)", self._channel)
        self._connected = False
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        self._ws = None
        logger.info("XunfeiRealtimeSTT [%s]: closed", self._channel)

    # ── internals ─────────────────────────────────────────────────────────────

    def _build_url(self) -> str:
        """生成带鉴权签名的 WebSocket URL。"""
        tz = timezone(timedelta(hours=8))
        utc_str = datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S+0800")
        conn_uuid = str(uuid.uuid4())

        params: dict[str, str] = {
            "appId": self._app_id,
            "accessKeyId": self._access_key_id,
            "uuid": conn_uuid,
            "utc": utc_str,
            "audio_encode": "pcm_s16le",
            "lang": "autodialect",
            "samplerate": str(_SAMPLE_RATE),
            "pd": "tech",
        }

        # 升序排序后逐个 URL-encode key/value，拼接 baseString
        sorted_keys = sorted(params.keys())
        base_string = "&".join(
            f"{quote(k, safe='')}={quote(params[k], safe='')}"
            for k in sorted_keys
        )

        mac = hmac.new(
            self._access_key_secret.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha1,
        )
        signature = base64.b64encode(mac.digest()).decode()
        params["signature"] = signature

        return f"{_WSS_BASE}?{urlencode(params)}"

    async def _recv_loop(self) -> None:
        """后台 task：持续从 WS 接收讯飞 ASR 响应，解析后入队。

        讯飞 LLM 版响应有两种格式：
          握手成功: {"action": "started", "code": "0", "sid": "..."}
                   或 {"msg_type": "started", ...}（文档表格与示例存在差异，兼容两种）
          识别结果: {"msg_type": "result", "res_type": "asr", "data": {...}}
          引擎报错: {"msg_type": "result", "res_type": "frc", "data": {"desc": "...", "normal": false}}
          服务报错: {"action": "error", "code": "35001", "desc": "..."}

        讯飞 ASR 存在已知行为：上一句的末尾标点会出现在下一句结果文本的开头。
        此处通过缓冲上一个最终结果（type=0），在下一条结果到来时将开头的标点归还给上一句，
        再将修正后的最终结果入队，确保每句话首尾标点位置正确。
        """
        pending_fin_text: str | None = None  # 已识别但尚未入队的上一句最终结果文本

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

                # ── 握手成功（文档 table 写 action，示例写 msg_type，兼容两种）
                msg_type = msg.get("msg_type") or msg.get("action", "")
                if msg_type == "started":
                    self._session_id = msg.get("sid")
                    logger.debug(
                        "XunfeiRealtimeSTT [%s]: session started sid=%s",
                        self._channel, self._session_id,
                    )
                    continue

                # ── 服务层错误（action=error 或顶层 code 非 0）
                code = str(msg.get("code", "0"))
                if msg_type == "error" or code != "0":
                    logger.warning(
                        "XunfeiRealtimeSTT [%s]: service error code=%s desc=%s",
                        self._channel, code, msg.get("desc", ""),
                    )
                    # 致命错误：鉴权失败、用量不足、引擎异常断连
                    if code in {"35001", "35002", "37008"}:
                        self._connected = False
                        break
                    continue

                # ── 引擎层错误（res_type=frc，错误嵌在 data 中）
                if msg.get("res_type") == "frc":
                    err_data = msg.get("data", {})
                    logger.warning(
                        "XunfeiRealtimeSTT [%s]: engine error desc=%s",
                        self._channel, err_data.get("desc", ""),
                    )
                    continue

                # ── 识别结果（res_type=asr）
                if msg.get("res_type") != "asr":
                    continue

                data = msg.get("data")
                if not data:
                    continue

                text, is_final = self._extract_text(data)
                if not text:
                    continue

                # 将开头的孤立标点归还给上一句最终结果
                leading_punct = ""
                while text and text[0] in _LEADING_PUNCT:
                    leading_punct += text[0]
                    text = text[1:]

                if leading_punct:
                    await _flush_pending(extra_punct=leading_punct)
                else:
                    await _flush_pending()

                if not text:
                    # 本条结果仅含标点，已合并到上一句，无需再入队
                    continue

                if is_final:
                    # 缓冲最终结果，等待下一条结果确认其末尾标点已到位
                    pending_fin_text = text
                else:
                    await self._recv_queue.put(TranscriptSegment(
                        text=text,
                        source=self._channel,
                        is_final=False,
                        timestamp=datetime.now(),
                    ))

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("XunfeiRealtimeSTT [%s]: recv loop error", self._channel)
        finally:
            await _flush_pending()
            self._connected = False
            logger.debug(
                "XunfeiRealtimeSTT [%s]: recv loop exited, _connected set to False",
                self._channel,
            )

    @staticmethod
    def _extract_text(data: dict) -> tuple[str, bool]:
        """从讯飞响应 data 字段提取拼接文本和 is_final 标志。"""
        try:
            st = data["cn"]["st"]
            is_final = st.get("type") == "0"
            words: list[str] = []
            for rt in st.get("rt", []):
                for ws_item in rt.get("ws", []):
                    for cw in ws_item.get("cw", []):
                        w = cw.get("w", "")
                        wp = cw.get("wp", "n")
                        if wp != "g" and w:  # 跳过分段标识（wp="g"）
                            words.append(w)
            return "".join(words), is_final
        except (KeyError, TypeError):
            return "", False

    async def _reconnect(self) -> None:
        """断连后延迟重连。"""
        if self._reconnecting or self._closed:
            return
        self._reconnecting = True
        try:
            logger.info(
                "XunfeiRealtimeSTT [%s]: reconnecting in %.1fs...",
                self._channel, _RECONNECT_DELAY_SEC,
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
            self._session_id = None
            await self.connect()
            logger.info(
                "XunfeiRealtimeSTT [%s]: reconnect %s",
                self._channel, "ok" if self._connected else "failed",
            )
        except Exception:
            logger.exception("XunfeiRealtimeSTT [%s]: reconnect error", self._channel)
        finally:
            self._reconnecting = False

    async def _queue_iter(self) -> AsyncIterator[TranscriptSegment]:
        """从内部队列异步产出 TranscriptSegment。"""
        while True:
            segment = await self._recv_queue.get()
            yield segment


__all__ = ["XunfeiRealtimeSTT"]
