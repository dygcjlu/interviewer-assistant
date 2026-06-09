---
change: add-volc-asr
design-doc: docs/superpowers/specs/2026-06-09-add-volc-asr-design.md
base-ref: 6824645f2057fa684c91ff009c2f8053e38323ce
---

# Add Volc (ByteDance) Realtime ASR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `VolcRealtimeSTT` (火山引擎豆包 BigModel ASR) as a third STT engine option, selectable via `STT_ENGINE=volc` in `.env`, implementing the same `connect / send_audio / receive / close` protocol as `BaiduRealtimeSTT`.

**Architecture:** A new `src/audio/volc_stt.py` containing three pure binary-framing helpers plus the `VolcRealtimeSTT` class. The class is structurally symmetric to `BaiduRealtimeSTT`: WebSocket-based, queue-backed `receive()`, auto-reconnect on disconnect, silent no-op when credentials are absent. `src/main.py` gains one extra `elif` branch; everything else is untouched.

**Tech Stack:** Python 3.12, `websockets` (already in requirements), `struct`/`json`/`uuid`/`gzip` stdlib — no new PyPI dependencies.

---

### Task 1: Config — Add Volc credentials to Settings

**Files:**
- Modify: `src/config.py`
- Modify: `.env`

- [ ] **Step 1: Add three Volc fields to Settings**

  Open `src/config.py`. After the `XUNFEI_*` block (lines ~46-50), add:

  ```python
  # 火山引擎实时 ASR（豆包 BigModel）
  VOLC_APP_KEY: str = ""
  VOLC_ACCESS_KEY: str = ""
  VOLC_RESOURCE_ID: str = "volc.bigasr.sauc.duration"
  ```

  Also update the comment on `STT_ENGINE` (line ~52):
  ```python
  # STT 引擎选择：baidu（默认）| xunfei | volc
  STT_ENGINE: str = "baidu"
  ```

- [ ] **Step 2: Add placeholder entries to .env**

  Append to `.env` (find the block with `XUNFEI_` entries and add after it):

  ```dotenv
  # 火山引擎豆包大模型实时 ASR（STT_ENGINE=volc 时有效）
  VOLC_APP_KEY=
  VOLC_ACCESS_KEY=
  VOLC_RESOURCE_ID=volc.bigasr.sauc.duration
  ```

- [ ] **Step 3: Verify Settings loads without error**

  Run:
  ```
  .venv\Scripts\python -c "from src.config import get_settings; s = get_settings(); print(s.VOLC_APP_KEY, s.VOLC_ACCESS_KEY, s.VOLC_RESOURCE_ID)"
  ```
  Expected output: `  volc.bigasr.sauc.duration` (empty strings + default resource ID)

- [ ] **Step 4: Commit**

  ```bash
  git add src/config.py .env
  git commit -m "feat(config): add VOLC_APP_KEY / VOLC_ACCESS_KEY / VOLC_RESOURCE_ID settings"
  ```

---

### Task 2: Binary helpers — TDD for frame encoding/decoding

This task creates the three pure functions that implement the Volc binary protocol. We write all the tests first, then implement.

**Files:**
- Create: `src/audio/volc_stt.py` (helpers only, no class yet)
- Create: `tests/unit/test_volc_stt.py`

- [ ] **Step 1: Create test file with tests for _build_full_client_request**

  Create `tests/unit/test_volc_stt.py`:

  ```python
  """Unit tests for VolcRealtimeSTT binary protocol helpers."""
  from __future__ import annotations

  import json
  import struct

  import pytest


  @pytest.mark.unit
  class TestBuildFullClientRequest:
      def test_header_bytes(self):
          from src.audio.volc_stt import _build_full_client_request

          payload = b'{"key":"value"}'
          frame = _build_full_client_request(payload)

          assert frame[0] == 0x11  # protocol_version=1, header_size=1
          assert frame[1] == 0x10  # msg_type=0001(full client), flags=0000
          assert frame[2] == 0x10  # serialization=0001(JSON), compression=0000
          assert frame[3] == 0x00  # reserved

      def test_payload_size_big_endian(self):
          from src.audio.volc_stt import _build_full_client_request

          payload = b'{"key":"value"}'  # 15 bytes
          frame = _build_full_client_request(payload)

          size = struct.unpack(">I", frame[4:8])[0]
          assert size == len(payload)

      def test_payload_content(self):
          from src.audio.volc_stt import _build_full_client_request

          payload = b'{"hello":"world"}'
          frame = _build_full_client_request(payload)

          assert frame[8:] == payload

      def test_total_length(self):
          from src.audio.volc_stt import _build_full_client_request

          payload = b'{"a":"b"}'
          frame = _build_full_client_request(payload)

          assert len(frame) == 4 + 4 + len(payload)


  @pytest.mark.unit
  class TestBuildAudioFrame:
      def test_normal_frame_header(self):
          from src.audio.volc_stt import _build_audio_frame

          frame = _build_audio_frame(b"\x00" * 6400, seq=1, is_last=False)

          assert frame[0] == 0x11
          assert frame[1] == 0x21  # msg_type=0010, flags=0001 (positive seq)
          assert frame[2] == 0x00  # raw, no compression
          assert frame[3] == 0x00

      def test_last_frame_header(self):
          from src.audio.volc_stt import _build_audio_frame

          frame = _build_audio_frame(b"", seq=5, is_last=True)

          assert frame[1] == 0x23  # msg_type=0010, flags=0011 (negative seq / last)

      def test_normal_frame_sequence_positive(self):
          from src.audio.volc_stt import _build_audio_frame

          frame = _build_audio_frame(b"\x00" * 100, seq=3, is_last=False)

          seq_val = struct.unpack(">i", frame[4:8])[0]  # signed big-endian
          assert seq_val == 3

      def test_last_frame_sequence_negative(self):
          from src.audio.volc_stt import _build_audio_frame

          frame = _build_audio_frame(b"", seq=5, is_last=True)

          seq_val = struct.unpack(">i", frame[4:8])[0]  # signed big-endian
          assert seq_val == -5

      def test_payload_size_field(self):
          from src.audio.volc_stt import _build_audio_frame

          audio = b"\xAB" * 6400
          frame = _build_audio_frame(audio, seq=1, is_last=False)

          size = struct.unpack(">I", frame[8:12])[0]
          assert size == 6400

      def test_payload_content(self):
          from src.audio.volc_stt import _build_audio_frame

          audio = b"\x01\x02\x03" * 100
          frame = _build_audio_frame(audio, seq=2, is_last=False)

          assert frame[12:] == audio

      def test_total_length(self):
          from src.audio.volc_stt import _build_audio_frame

          audio = b"\x00" * 6400
          frame = _build_audio_frame(audio, seq=1, is_last=False)

          # 4 header + 4 seq + 4 payload_size + audio
          assert len(frame) == 12 + 6400


  @pytest.mark.unit
  class TestParseServerResponse:
      def _make_response(self, payload: dict, has_sequence: bool = False, seq: int = 0) -> bytes:
          """Build a minimal Full Server Response binary frame."""
          payload_bytes = json.dumps(payload).encode()
          # flags: bit0 = has_sequence
          flags = 0x01 if has_sequence else 0x00
          header = bytes([0x11, 0x90 | flags, 0x10, 0x00])
          parts = [header]
          if has_sequence:
              parts.append(struct.pack(">i", seq))
          parts.append(struct.pack(">I", len(payload_bytes)))
          parts.append(payload_bytes)
          return b"".join(parts)

      def _make_error_frame(self, error_code: int, message: str) -> bytes:
          msg_bytes = message.encode()
          header = bytes([0x11, 0xF0, 0x00, 0x00])
          return header + struct.pack(">I", error_code) + struct.pack(">I", len(msg_bytes)) + msg_bytes

      def test_parses_definite_utterance(self):
          from src.audio.volc_stt import _parse_server_response

          data = self._make_response({
              "result": {
                  "utterances": [
                      {"text": "你好", "definite": True}
                  ]
              }
          })
          result = _parse_server_response(data)

          assert result is not None
          assert result["utterances"][0]["text"] == "你好"
          assert result["utterances"][0]["definite"] is True

      def test_parses_non_definite_utterance(self):
          from src.audio.volc_stt import _parse_server_response

          data = self._make_response({
              "result": {
                  "utterances": [
                      {"text": "正在识别", "definite": False}
                  ]
              }
          })
          result = _parse_server_response(data)

          assert result is not None
          assert result["utterances"][0]["definite"] is False

      def test_returns_none_on_empty_result(self):
          from src.audio.volc_stt import _parse_server_response

          data = self._make_response({"result": {}})
          result = _parse_server_response(data)

          assert result is None

      def test_returns_none_on_error_frame(self):
          from src.audio.volc_stt import _parse_server_response

          data = self._make_error_frame(1001, "auth failed")
          result = _parse_server_response(data)

          assert result is None

      def test_returns_none_on_malformed_bytes(self):
          from src.audio.volc_stt import _parse_server_response

          result = _parse_server_response(b"\xFF\xFF")
          assert result is None

      def test_parses_response_with_sequence(self):
          from src.audio.volc_stt import _parse_server_response

          data = self._make_response(
              {"result": {"utterances": [{"text": "测试", "definite": True}]}},
              has_sequence=True, seq=3
          )
          result = _parse_server_response(data)

          assert result is not None
          assert result["utterances"][0]["text"] == "测试"
  ```

- [ ] **Step 2: Run tests — expect ImportError (RED)**

  ```
  .venv\Scripts\python -m pytest tests/unit/test_volc_stt.py -v 2>&1 | head -30
  ```
  Expected: all tests fail with `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Create volc_stt.py with the three helper functions**

  Create `src/audio/volc_stt.py`:

  ```python
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
  ```

- [ ] **Step 4: Run protocol helper tests — expect GREEN**

  ```
  .venv\Scripts\python -m pytest tests/unit/test_volc_stt.py -v
  ```
  Expected: all tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add src/audio/volc_stt.py tests/unit/test_volc_stt.py
  git commit -m "feat(volc-stt): add binary frame helpers with unit tests"
  ```

---

### Task 3: VolcRealtimeSTT class — TDD for class behavior

**Files:**
- Modify: `src/audio/volc_stt.py` (add class)
- Modify: `tests/unit/test_volc_stt.py` (add class tests)

- [ ] **Step 1: Add class-level tests to test_volc_stt.py**

  Append these test classes to `tests/unit/test_volc_stt.py`:

  ```python
  import asyncio
  from unittest.mock import AsyncMock, MagicMock, patch


  @pytest.mark.unit
  class TestVolcRealtimeSTTCredentialCheck:
      @pytest.mark.asyncio
      async def test_connect_silent_when_no_credentials(self):
          """connect() returns without connecting when credentials are absent."""
          from src.audio.volc_stt import VolcRealtimeSTT

          stt = VolcRealtimeSTT(channel="candidate")
          # No credentials set (defaults to empty strings)
          with patch("src.audio.volc_stt.ws_connect") as mock_connect:
              await stt.connect()
              mock_connect.assert_not_called()
          assert not stt._connected

      @pytest.mark.asyncio
      async def test_receive_yields_nothing_when_not_connected(self):
          """receive() produces no segments when connection was never established."""
          from src.audio.volc_stt import VolcRealtimeSTT

          stt = VolcRealtimeSTT(channel="candidate")

          async def collect_with_timeout():
              results = []
              async for seg in stt.receive():
                  results.append(seg)
                  break  # would block forever if no sentinel
              return results

          # Should not hang — queue is empty, just verify no items come immediately
          # We put a sentinel to unblock
          await stt._recv_queue.put(None)  # type: ignore[arg-type]
          segments = []
          async for seg in stt.receive():
              if seg is None:
                  break
              segments.append(seg)
          assert segments == []


  @pytest.mark.unit
  class TestVolcRealtimeSTTSendAudio:
      @pytest.mark.asyncio
      async def test_send_audio_buffers_below_threshold(self):
          """Audio smaller than 6400 bytes is buffered, not sent."""
          from src.audio.volc_stt import VolcRealtimeSTT, _SEND_CHUNK_BYTES

          stt = VolcRealtimeSTT(channel="candidate")
          stt._connected = True
          mock_ws = AsyncMock()
          stt._ws = mock_ws

          small_audio = b"\x00" * (_SEND_CHUNK_BYTES - 1)
          await stt.send_audio(small_audio)

          mock_ws.send.assert_not_called()
          assert len(stt._audio_buf) == _SEND_CHUNK_BYTES - 1

      @pytest.mark.asyncio
      async def test_send_audio_flushes_at_threshold(self):
          """Exactly 6400 bytes triggers a send."""
          from src.audio.volc_stt import VolcRealtimeSTT, _SEND_CHUNK_BYTES

          stt = VolcRealtimeSTT(channel="candidate")
          stt._connected = True
          mock_ws = AsyncMock()
          stt._ws = mock_ws

          audio = b"\x01" * _SEND_CHUNK_BYTES
          await stt.send_audio(audio)

          mock_ws.send.assert_called_once()
          sent_frame = mock_ws.send.call_args[0][0]
          # verify it's a binary frame (starts with 0x11)
          assert sent_frame[0] == 0x11
          # buffer should be empty after exact flush
          assert stt._audio_buf == b""

      @pytest.mark.asyncio
      async def test_send_audio_retains_remainder(self):
          """Bytes beyond 6400 remain in the buffer."""
          from src.audio.volc_stt import VolcRealtimeSTT, _SEND_CHUNK_BYTES

          stt = VolcRealtimeSTT(channel="candidate")
          stt._connected = True
          mock_ws = AsyncMock()
          stt._ws = mock_ws

          audio = b"\x02" * (_SEND_CHUNK_BYTES + 100)
          await stt.send_audio(audio)

          mock_ws.send.assert_called_once()
          assert len(stt._audio_buf) == 100
  ```

- [ ] **Step 2: Run new tests — expect FAIL (RED)**

  ```
  .venv\Scripts\python -m pytest tests/unit/test_volc_stt.py::TestVolcRealtimeSTTCredentialCheck tests/unit/test_volc_stt.py::TestVolcRealtimeSTTSendAudio -v
  ```
  Expected: `ImportError: cannot import name 'VolcRealtimeSTT'`

- [ ] **Step 3: Add VolcRealtimeSTT class to volc_stt.py**

  Append after the helper functions in `src/audio/volc_stt.py`:

  ```python
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
          self._app_key: str = getattr(settings, "VOLC_APP_KEY", "")
          self._access_key: str = getattr(settings, "VOLC_ACCESS_KEY", "")
          self._resource_id: str = getattr(settings, "VOLC_RESOURCE_ID", "volc.bigasr.sauc.duration")

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
              req_payload = json.dumps({
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
              }).encode()
              await self._ws.send(_build_full_client_request(req_payload))
              self._connected = True
              self._seq = 0
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
                  await self._ws.send(_build_audio_frame(self._audio_buf, seq=self._seq, is_last=True))
                  self._audio_buf = b""
                  await self._ws.close()
              except Exception:
                  logger.debug("VolcRealtimeSTT [%s]: close error (ignored)", self._channel)
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
                      await self._recv_queue.put(TranscriptSegment(
                          text=text,
                          source=self._channel,
                          is_final=is_final,
                          timestamp=datetime.now(),
                      ))
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
              logger.info("VolcRealtimeSTT [%s]: reconnect %s", self._channel,
                          "ok" if self._connected else "failed")
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
  ```

- [ ] **Step 4: Run all volc_stt tests — expect GREEN**

  ```
  .venv\Scripts\python -m pytest tests/unit/test_volc_stt.py -v
  ```
  Expected: all tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add src/audio/volc_stt.py tests/unit/test_volc_stt.py
  git commit -m "feat(volc-stt): implement VolcRealtimeSTT class with unit tests"
  ```

---

### Task 4: Factory wiring — Add volc branch to main.py

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Add the elif volc branch**

  In `src/main.py`, find this block (around line 152):

  ```python
  if settings.STT_ENGINE == "xunfei":
      from src.audio.xunfei_stt import XunfeiRealtimeSTT
      candidate_stt = XunfeiRealtimeSTT(channel="candidate")
      interviewer_stt = XunfeiRealtimeSTT(channel="interviewer")
      logger.info("Audio: using XunfeiRealtimeSTT")
  else:
      from src.audio.baidu_stt import BaiduRealtimeSTT
      candidate_stt = BaiduRealtimeSTT(channel="candidate")
      interviewer_stt = BaiduRealtimeSTT(channel="interviewer")
      logger.info("Audio: using BaiduRealtimeSTT")
  ```

  Replace with:

  ```python
  if settings.STT_ENGINE == "xunfei":
      from src.audio.xunfei_stt import XunfeiRealtimeSTT
      candidate_stt = XunfeiRealtimeSTT(channel="candidate")
      interviewer_stt = XunfeiRealtimeSTT(channel="interviewer")
      logger.info("Audio: using XunfeiRealtimeSTT")
  elif settings.STT_ENGINE == "volc":
      from src.audio.volc_stt import VolcRealtimeSTT
      candidate_stt = VolcRealtimeSTT(channel="candidate")
      interviewer_stt = VolcRealtimeSTT(channel="interviewer")
      logger.info("Audio: using VolcRealtimeSTT")
  else:
      from src.audio.baidu_stt import BaiduRealtimeSTT
      candidate_stt = BaiduRealtimeSTT(channel="candidate")
      interviewer_stt = BaiduRealtimeSTT(channel="interviewer")
      logger.info("Audio: using BaiduRealtimeSTT")
  ```

- [ ] **Step 2: Verify import works**

  ```
  .venv\Scripts\python -c "import src.main" 2>&1 | head -5
  ```
  Expected: no errors (may print some startup warnings about missing API keys, that's fine).

- [ ] **Step 3: Run full unit test suite**

  ```
  .venv\Scripts\python -m pytest tests/unit/ -v --tb=short 2>&1 | tail -20
  ```
  Expected: all existing tests PASS, new volc tests PASS.

- [ ] **Step 4: Commit**

  ```bash
  git add src/main.py
  git commit -m "feat(main): add STT_ENGINE=volc factory branch for VolcRealtimeSTT"
  ```

---

## Verification Checklist

After all tasks are complete:

- [ ] `pytest tests/unit/test_volc_stt.py -v` — all 15 tests PASS
- [ ] `pytest tests/unit/ -v` — no regressions
- [ ] `python -c "from src.config import get_settings; s=get_settings(); print(s.VOLC_APP_KEY)"` — no error
- [ ] Manual smoke test (optional, requires real credentials): set `STT_ENGINE=volc` + Volc keys in `.env`, start server, begin interview, confirm real-time captions appear
