"""Unit tests for VolcRealtimeSTT binary protocol helpers."""

from __future__ import annotations

import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def aiter_from_list(items):
    """Helper: async iterator that yields items from a list."""
    for item in items:
        yield item


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

        audio = b"\xab" * 6400
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
    def _make_response(
        self, payload: dict, has_sequence: bool = False, seq: int = 0
    ) -> bytes:
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
        return (
            header
            + struct.pack(">I", error_code)
            + struct.pack(">I", len(msg_bytes))
            + msg_bytes
        )

    def test_parses_definite_utterance(self):
        from src.audio.volc_stt import _parse_server_response

        data = self._make_response(
            {"result": {"utterances": [{"text": "你好", "definite": True}]}}
        )
        result = _parse_server_response(data)

        assert result is not None
        assert result["utterances"][0]["text"] == "你好"
        assert result["utterances"][0]["definite"] is True

    def test_parses_non_definite_utterance(self):
        from src.audio.volc_stt import _parse_server_response

        data = self._make_response(
            {"result": {"utterances": [{"text": "正在识别", "definite": False}]}}
        )
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

        result = _parse_server_response(b"\xff\xff")
        assert result is None

    def test_parses_response_with_sequence(self):
        from src.audio.volc_stt import _parse_server_response

        data = self._make_response(
            {"result": {"utterances": [{"text": "测试", "definite": True}]}},
            has_sequence=True,
            seq=3,
        )
        result = _parse_server_response(data)

        assert result is not None
        assert result["utterances"][0]["text"] == "测试"

    def test_returns_none_on_empty_utterances_list(self):
        from src.audio.volc_stt import _parse_server_response

        data = self._make_response({"result": {"utterances": []}})
        result = _parse_server_response(data)

        assert result is None


@pytest.mark.unit
class TestVolcRealtimeSTTCredentialCheck:
    @pytest.mark.asyncio
    async def test_connect_silent_when_no_credentials(self):
        """connect() returns without connecting when credentials are absent."""
        from src.audio.volc_stt import VolcRealtimeSTT

        stt = VolcRealtimeSTT(channel="candidate")
        # No credentials set (defaults to empty strings in test environment)
        with patch("src.audio.volc_stt.ws_connect") as mock_connect:
            await stt.connect()
            mock_connect.assert_not_called()
        assert not stt._connected

    @pytest.mark.asyncio
    async def test_receive_yields_nothing_when_not_connected(self):
        """receive() produces no segments when connection was never established."""
        from src.audio.volc_stt import VolcRealtimeSTT

        stt = VolcRealtimeSTT(channel="candidate")

        # Put a sentinel None to unblock the iterator
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
        from src.audio.volc_stt import _SEND_CHUNK_BYTES, VolcRealtimeSTT

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
        from src.audio.volc_stt import _SEND_CHUNK_BYTES, VolcRealtimeSTT

        stt = VolcRealtimeSTT(channel="candidate")
        stt._connected = True
        mock_ws = AsyncMock()
        stt._ws = mock_ws

        audio = b"\x01" * _SEND_CHUNK_BYTES
        await stt.send_audio(audio)

        mock_ws.send.assert_called_once()
        sent_frame = mock_ws.send.call_args[0][0]
        # verify it's a binary audio frame (starts with 0x11)
        assert sent_frame[0] == 0x11
        # buffer should be empty after exact flush
        assert stt._audio_buf == b""

    @pytest.mark.asyncio
    async def test_send_audio_retains_remainder(self):
        """Bytes beyond 6400 remain in the buffer."""
        from src.audio.volc_stt import _SEND_CHUNK_BYTES, VolcRealtimeSTT

        stt = VolcRealtimeSTT(channel="candidate")
        stt._connected = True
        mock_ws = AsyncMock()
        stt._ws = mock_ws

        audio = b"\x02" * (_SEND_CHUNK_BYTES + 100)
        await stt.send_audio(audio)

        mock_ws.send.assert_called_once()
        assert len(stt._audio_buf) == 100


@pytest.mark.unit
class TestVolcRealtimeSTTClose:
    @pytest.mark.asyncio
    async def test_close_sends_last_frame_and_closes_ws(self):
        """close() sends the last-packet frame with is_final=True before closing ws."""
        from src.audio.volc_stt import VolcRealtimeSTT

        stt = VolcRealtimeSTT(channel="candidate")
        stt._connected = True
        stt._seq = 3
        mock_ws = AsyncMock()
        stt._ws = mock_ws
        stt._recv_task = None

        await stt.close()

        assert mock_ws.send.called
        sent_frame = mock_ws.send.call_args[0][0]
        # Last packet: byte[1] must be 0x23 (audio only, last/negative seq)
        assert sent_frame[1] == 0x23
        mock_ws.close.assert_called_once()
        assert not stt._connected
        assert stt._ws is None

    @pytest.mark.asyncio
    async def test_close_when_not_connected_is_noop(self):
        """close() on an unconnected instance does not raise."""
        from src.audio.volc_stt import VolcRealtimeSTT

        stt = VolcRealtimeSTT(channel="candidate")
        # Never connected
        await stt.close()

        assert stt._closed
        assert not stt._connected


@pytest.mark.unit
class TestVolcRealtimeSTTRecvLoop:
    @pytest.mark.asyncio
    async def test_recv_loop_puts_segment_for_definite_utterance(self):
        """_recv_loop parses a binary frame and puts a final TranscriptSegment in the queue."""
        import json
        import struct

        from src.audio.volc_stt import VolcRealtimeSTT

        payload = json.dumps(
            {"result": {"utterances": [{"text": "你好世界", "definite": True}]}}
        ).encode()
        header = bytes([0x11, 0x90, 0x10, 0x00])
        frame = header + struct.pack(">I", len(payload)) + payload

        stt = VolcRealtimeSTT(channel="candidate")
        stt._connected = True

        mock_ws = MagicMock()
        mock_ws.__aiter__ = MagicMock(return_value=aiter_from_list([frame]))
        stt._ws = mock_ws

        await stt._recv_loop()

        seg = stt._recv_queue.get_nowait()
        assert seg.text == "你好世界"
        assert seg.is_final is True
        assert seg.source == "candidate"
        assert not stt._connected  # set to False in finally

    @pytest.mark.asyncio
    async def test_recv_loop_puts_segment_for_non_definite_utterance(self):
        """_recv_loop emits is_final=False for definite=False utterances."""
        import json
        import struct

        from src.audio.volc_stt import VolcRealtimeSTT

        payload = json.dumps(
            {"result": {"utterances": [{"text": "正在识别", "definite": False}]}}
        ).encode()
        header = bytes([0x11, 0x90, 0x10, 0x00])
        frame = header + struct.pack(">I", len(payload)) + payload

        stt = VolcRealtimeSTT(channel="candidate")
        stt._connected = True
        mock_ws = MagicMock()
        mock_ws.__aiter__ = MagicMock(return_value=aiter_from_list([frame]))
        stt._ws = mock_ws

        await stt._recv_loop()

        seg = stt._recv_queue.get_nowait()
        assert seg.is_final is False
        assert seg.text == "正在识别"

    @pytest.mark.asyncio
    async def test_recv_loop_skips_empty_text(self):
        """_recv_loop ignores utterances with empty text."""
        import json
        import struct

        from src.audio.volc_stt import VolcRealtimeSTT

        payload = json.dumps(
            {"result": {"utterances": [{"text": "", "definite": True}]}}
        ).encode()
        header = bytes([0x11, 0x90, 0x10, 0x00])
        frame = header + struct.pack(">I", len(payload)) + payload

        stt = VolcRealtimeSTT(channel="candidate")
        stt._connected = True
        mock_ws = MagicMock()
        mock_ws.__aiter__ = MagicMock(return_value=aiter_from_list([frame]))
        stt._ws = mock_ws

        await stt._recv_loop()

        assert stt._recv_queue.empty()
