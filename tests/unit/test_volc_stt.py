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
