"""独立脚本：使用科大讯飞实时 ASR 转写本地 WAV 文件。

用法：
    python scripts/transcribe_xunfei.py <wav_file> [--output <txt_file>]

WAV 要求：单声道、16kHz、16-bit PCM（与录音格式一致）。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import sys
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from websockets.asyncio.client import connect as ws_connect

# ── 常量 ─────────────────────────────────────────────────────────────────────
_WSS_BASE = "wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1"
_SAMPLE_RATE = 16000
_SEND_CHUNK_BYTES = 1280          # 1280 bytes = 40ms @ 16kHz 16-bit mono
_REAL_TIME_INTERVAL_SEC = 0.04    # 实时速率：40ms/chunk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_credentials() -> tuple[str, str, str]:
    """从项目根目录的 .env 文件读取讯飞凭据。"""
    env_path = Path(__file__).parent.parent / ".env"
    creds: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                creds[k.strip()] = v.strip()
    app_id = creds.get("XUNFEI_APP_ID", "")
    key_id = creds.get("XUNFEI_ACCESS_KEY_ID", "")
    key_secret = creds.get("XUNFEI_ACCESS_KEY_SECRET", "")
    return app_id, key_id, key_secret


def _build_url(app_id: str, key_id: str, key_secret: str) -> str:
    tz = timezone(timedelta(hours=8))
    utc_str = datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S+0800")
    conn_uuid = str(uuid.uuid4())

    params: dict[str, str] = {
        "appId": app_id,
        "accessKeyId": key_id,
        "uuid": conn_uuid,
        "utc": utc_str,
        "audio_encode": "pcm_s16le",
        "lang": "autodialect",
        "samplerate": str(_SAMPLE_RATE),
        "pd": "tech",
    }

    sorted_keys = sorted(params.keys())
    base_string = "&".join(
        f"{quote(k, safe='')}={quote(params[k], safe='')}"
        for k in sorted_keys
    )
    mac = hmac.new(
        key_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    )
    signature = base64.b64encode(mac.digest()).decode()
    params["signature"] = signature
    return f"{_WSS_BASE}?{urlencode(params)}"


def _extract_text(data: dict) -> tuple[str, bool]:
    try:
        st = data["cn"]["st"]
        is_final = st.get("type") == "0"
        words: list[str] = []
        for rt in st.get("rt", []):
            for ws_item in rt.get("ws", []):
                for cw in ws_item.get("cw", []):
                    w = cw.get("w", "")
                    wp = cw.get("wp", "n")
                    if wp != "g" and w:
                        words.append(w)
        return "".join(words), is_final
    except (KeyError, TypeError):
        return "", False


async def transcribe(wav_path: Path, output_path: Path, speed: float = 4.0, max_seconds: float | None = None) -> None:
    app_id, key_id, key_secret = _load_credentials()
    if not app_id or not key_id or not key_secret:
        logger.error("讯飞凭据未配置，请检查 .env 文件中的 XUNFEI_* 配置项")
        sys.exit(1)

    with wave.open(str(wav_path)) as wf:
        n_channels = wf.getnchannels()
        framerate = wf.getframerate()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        pcm_data = wf.readframes(n_frames)

    duration = n_frames / framerate
    logger.info(
        "WAV: %s | channels=%d rate=%d width=%d frames=%d duration=%.1fs",
        wav_path.name, n_channels, framerate, sampwidth, n_frames, duration,
    )
    if framerate != _SAMPLE_RATE or sampwidth != 2 or n_channels != 1:
        logger.error(
            "格式不符：需要单声道 16kHz 16-bit PCM，实际 channels=%d rate=%d width=%d",
            n_channels, framerate, sampwidth,
        )
        sys.exit(1)

    # 截取前 max_seconds 秒
    if max_seconds is not None and max_seconds < duration:
        max_frames = int(max_seconds * framerate)
        pcm_data = pcm_data[: max_frames * sampwidth]
        logger.info("截取前 %.1f 秒（%d 帧）", max_seconds, max_frames)

    interval = _REAL_TIME_INTERVAL_SEC / speed
    estimated = len(pcm_data) / _SEND_CHUNK_BYTES * interval
    logger.info("发送速率：%.1fx 实时，预计耗时 %.0f 秒", speed, estimated)

    url = _build_url(app_id, key_id, key_secret)
    total_bytes = len(pcm_data)
    sent_bytes = 0
    results: list[str] = []
    session_id: str | None = None

    logger.info("连接讯飞 ASR...")

    async with ws_connect(url) as ws:
        logger.info("已连接，开始发送音频...")

        async def recv_loop() -> None:
            nonlocal session_id
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("msg_type") or msg.get("action", "")
                if msg_type == "started":
                    session_id = msg.get("sid")
                    logger.info("握手成功 sid=%s", session_id)
                    continue

                code = str(msg.get("code", "0"))
                if msg_type == "error" or code != "0":
                    logger.warning("服务错误 code=%s desc=%s", code, msg.get("desc", ""))
                    continue

                if msg.get("res_type") == "frc":
                    logger.warning("引擎错误: %s", msg.get("data", {}).get("desc", ""))
                    continue

                if msg.get("res_type") != "asr":
                    continue

                data = msg.get("data")
                if not data:
                    continue

                text, is_final = _extract_text(data)
                if not text:
                    continue

                tag = "[final]" if is_final else "[partial]"
                logger.info("%s %s", tag, text)
                if is_final:
                    results.append(text)

        recv_task = asyncio.create_task(recv_loop())

        # 按 40ms 节拍推送音频
        offset = 0
        last_log_pct = 0
        while offset < total_bytes:
            chunk = pcm_data[offset: offset + _SEND_CHUNK_BYTES]
            offset += _SEND_CHUNK_BYTES
            sent_bytes += len(chunk)
            await ws.send(chunk)

            pct = int(sent_bytes / total_bytes * 100)
            if pct >= last_log_pct + 10:
                last_log_pct = pct
                logger.info("进度: %d%%（%d / %d 字节）", pct, sent_bytes, total_bytes)

            await asyncio.sleep(interval)

        # 发送结束帧
        end_frame = {"end": True, "sessionId": session_id or ""}
        await ws.send(json.dumps(end_frame))
        logger.info("音频发送完毕，等待剩余结果...")

        # 等待最多 30 秒接收剩余结果
        try:
            await asyncio.wait_for(recv_task, timeout=30.0)
        except asyncio.TimeoutError:
            recv_task.cancel()
            logger.info("等待超时，收取结束")
        except Exception:
            pass

    full_text = "\n".join(results)
    output_path.write_text(full_text, encoding="utf-8")
    logger.info("转写完成，共 %d 句，已保存到 %s", len(results), output_path)
    print("\n=== 转写结果 ===")
    print(full_text)


def main() -> None:
    parser = argparse.ArgumentParser(description="讯飞 ASR 离线文件转写")
    parser.add_argument("wav", type=Path, help="WAV 文件路径")
    parser.add_argument("--output", "-o", type=Path, default=None, help="输出 txt 文件路径")
    parser.add_argument("--speed", "-s", type=float, default=4.0, help="发送速率倍数（默认 4x）")
    parser.add_argument("--max-seconds", "-t", type=float, default=None, help="只转写前 N 秒")
    args = parser.parse_args()

    wav_path: Path = args.wav
    if not wav_path.exists():
        logger.error("文件不存在: %s", wav_path)
        sys.exit(1)

    output_path: Path = args.output or wav_path.with_suffix(".xunfei.txt")
    asyncio.run(transcribe(wav_path, output_path, speed=args.speed, max_seconds=args.max_seconds))


if __name__ == "__main__":
    main()
