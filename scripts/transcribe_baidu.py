"""独立脚本：使用百度实时 ASR 转写本地 WAV 文件。

用法：
    python scripts/transcribe_baidu.py <wav_file> [--output <txt_file>] [--max-seconds N]

WAV 要求：单声道、16kHz、16-bit PCM。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
import wave
from pathlib import Path

from websockets.asyncio.client import connect as ws_connect

_WSS_URL = "wss://vop.baidu.com/realtime_asr"
_SAMPLE_RATE = 16000
_SEND_CHUNK_BYTES = 5120  # 百度建议 5120 字节 = 160ms @ 16kHz 16-bit mono
_REAL_TIME_INTERVAL_SEC = 0.16

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_credentials() -> tuple[str, str]:
    env_path = Path(__file__).parent.parent / ".env"
    creds: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                creds[k.strip()] = v.strip()
    return creds.get("BAIDU_APP_ID", ""), creds.get("BAIDU_API_KEY", "")


async def transcribe(
    wav_path: Path,
    output_path: Path,
    speed: float = 4.0,
    max_seconds: float | None = None,
) -> None:
    app_id, api_key = _load_credentials()
    if not app_id or not api_key:
        logger.error(
            "百度凭据未配置，请检查 .env 文件中的 BAIDU_APP_ID / BAIDU_API_KEY"
        )
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
        wav_path.name,
        n_channels,
        framerate,
        sampwidth,
        n_frames,
        duration,
    )
    if framerate != _SAMPLE_RATE or sampwidth != 2 or n_channels != 1:
        logger.error(
            "格式不符：需要单声道 16kHz 16-bit PCM，实际 channels=%d rate=%d width=%d",
            n_channels,
            framerate,
            sampwidth,
        )
        sys.exit(1)

    if max_seconds is not None and max_seconds < duration:
        max_frames = int(max_seconds * framerate)
        pcm_data = pcm_data[: max_frames * sampwidth]
        logger.info("截取前 %.1f 秒（%d 帧）", max_seconds, max_frames)

    interval = _REAL_TIME_INTERVAL_SEC / speed
    estimated = len(pcm_data) / _SEND_CHUNK_BYTES * interval
    logger.info("发送速率：%.1fx 实时，预计耗时 %.0f 秒", speed, estimated)

    sn = str(uuid.uuid4())
    url = f"{_WSS_URL}?sn={sn}"
    results: list[str] = []
    total_bytes = len(pcm_data)
    sent_bytes = 0

    logger.info("连接百度 ASR...")

    async with ws_connect(url) as ws:
        start_frame = {
            "type": "START",
            "data": {
                "appid": int(app_id),
                "appkey": api_key,
                "dev_pid": 15372,
                "cuid": "interviewer-assistant-compare",
                "format": "pcm",
                "sample": _SAMPLE_RATE,
            },
        }
        await ws.send(json.dumps(start_frame))
        logger.info("已发送 START 帧，开始发送音频...")

        async def recv_loop() -> None:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                err_no = msg.get("err_no", 0)
                msg_type = msg.get("type", "")

                if err_no != 0:
                    logger.warning(
                        "服务错误 err_no=%d err_msg=%s", err_no, msg.get("err_msg", "")
                    )
                    continue

                result = msg.get("result", "")
                if not result:
                    continue

                is_final = msg_type == "FIN_TEXT"
                tag = "[final]" if is_final else "[partial]"
                logger.info("%s %s", tag, result)
                if is_final:
                    results.append(result)

        recv_task = asyncio.create_task(recv_loop())

        offset = 0
        last_log_pct = 0
        while offset < total_bytes:
            chunk = pcm_data[offset : offset + _SEND_CHUNK_BYTES]
            offset += _SEND_CHUNK_BYTES
            sent_bytes += len(chunk)
            await ws.send(chunk)

            pct = int(sent_bytes / total_bytes * 100)
            if pct >= last_log_pct + 10:
                last_log_pct = pct
                logger.info("进度: %d%%（%d / %d 字节）", pct, sent_bytes, total_bytes)

            await asyncio.sleep(interval)

        await ws.send(json.dumps({"type": "FINISH"}))
        logger.info("音频发送完毕，等待剩余结果...")

        try:
            await asyncio.wait_for(recv_task, timeout=30.0)
        except TimeoutError:
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
    parser = argparse.ArgumentParser(description="百度 ASR 离线文件转写")
    parser.add_argument("wav", type=Path, help="WAV 文件路径")
    parser.add_argument("--output", "-o", type=Path, default=None)
    parser.add_argument(
        "--speed", "-s", type=float, default=4.0, help="发送速率倍数（默认 4x）"
    )
    parser.add_argument(
        "--max-seconds", "-t", type=float, default=None, help="只转写前 N 秒"
    )
    args = parser.parse_args()

    wav_path: Path = args.wav
    if not wav_path.exists():
        logger.error("文件不存在: %s", wav_path)
        sys.exit(1)

    output_path: Path = args.output or wav_path.with_suffix(".baidu.txt")
    asyncio.run(
        transcribe(
            wav_path, output_path, speed=args.speed, max_seconds=args.max_seconds
        )
    )


if __name__ == "__main__":
    main()
