"""实时采集浏览器（Bilibili等）系统声音并转文字。

原理：WASAPI Loopback 回采扬声器输出 → Baidu 实时 STT WebSocket → 打印文字

运行方式：
    python -m demo.bilibili_stt

按 Ctrl+C 停止。会话结束（无语音 / 超时）时自动重连。
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import signal
import sys
import threading
import time
import uuid

import numpy as np
import soundcard as sc
import websockets

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

APPID = 121087443
APPKEY = "eLpyLuxR0of5RWsn497uSdp0"
DEV_PID = 15372          # 普通话

SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_MS = 300           # 每帧录音时长
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
CHUNK_BYTES = 5120       # 每次发送给 STT 的 PCM 大小 (160ms)
BAIDU_WSS = "wss://vop.baidu.com/realtime_asr"

_stop = threading.Event()


# ── 录音线程 ────────────────────────────────────────────────────────────────

def record_loopback(audio_q: queue.Queue) -> None:
    """从 Loopback 设备持续录音，将 int16 PCM bytes 放入队列。"""
    speaker = sc.default_speaker()
    all_mics = sc.all_microphones(include_loopback=True)
    loopbacks = [m for m in all_mics if getattr(m, "isloopback", False)]

    if not loopbacks:
        print("[错误] 未找到 Loopback 设备，请确认系统使用 WASAPI 音频后端")
        _stop.set()
        return

    # 优先选和默认扬声器同名的 loopback
    mic = next((m for m in loopbacks if speaker.id in m.id), loopbacks[0])
    print(f"[采集] Loopback 设备: {mic.name}")

    try:
        with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS) as rec:
            while not _stop.is_set():
                raw = rec.record(numframes=FRAME_SAMPLES)
                pcm = (np.clip(raw.flatten(), -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                try:
                    audio_q.put_nowait(pcm)
                except queue.Full:
                    pass  # 丢弃过期帧，保持实时性
    except Exception as e:
        print(f"[录音错误] {e}")
        _stop.set()


# ── STT 会话（async）─────────────────────────────────────────────────────────

async def stt_session(audio_q: queue.Queue) -> None:
    """一次 Baidu STT WebSocket 会话：直到服务端关闭或出错。"""
    sn = str(uuid.uuid4())
    url = f"{BAIDU_WSS}?sn={sn}"

    async with websockets.connect(url) as ws:
        start_frame = {
            "type": "START",
            "data": {
                "appid": APPID,
                "appkey": APPKEY,
                "dev_pid": DEV_PID,
                "cuid": uuid.uuid4().hex,
                "format": "pcm",
                "sample": SAMPLE_RATE,
            },
        }
        await ws.send(json.dumps(start_frame))

        async def send_loop():
            buf = b""
            while not _stop.is_set():
                try:
                    chunk = audio_q.get(timeout=0.1)
                    buf += chunk
                    # 按 CHUNK_BYTES 切片发送
                    while len(buf) >= CHUNK_BYTES:
                        await ws.send(buf[:CHUNK_BYTES])
                        buf = buf[CHUNK_BYTES:]
                except queue.Empty:
                    pass
            # 发送 FINISH
            try:
                await ws.send(json.dumps({"type": "FINISH"}))
            except Exception:
                pass

        async def recv_loop():
            async for msg in ws:
                if isinstance(msg, bytes):
                    continue
                try:
                    data = json.loads(msg)
                except Exception:
                    continue
                msg_type = data.get("type", "")
                err_no = data.get("err_no", 0)
                result = data.get("result", "")

                if msg_type == "MID_TEXT" and result:
                    print(f"\r[中间] {result}          ", end="", flush=True)
                elif msg_type == "FIN_TEXT":
                    if err_no != 0:
                        if err_no != -3101:  # -3101 是正常的无语音超时
                            print(f"\n[STT错误] err_no={err_no} {data.get('err_msg', '')}")
                    elif result:
                        print(f"\r[最终] {result}          ")
                    return  # FIN_TEXT 表示会话结束，退出 recv_loop

        # 并发发送 + 接收
        sender = asyncio.create_task(send_loop())
        await recv_loop()
        sender.cancel()
        try:
            await sender
        except asyncio.CancelledError:
            pass


# ── 主循环 ───────────────────────────────────────────────────────────────────

async def run() -> None:
    audio_q: queue.Queue = queue.Queue(maxsize=100)

    rec_thread = threading.Thread(target=record_loopback, args=(audio_q,), daemon=True)
    rec_thread.start()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop.set)
        except NotImplementedError:
            pass

    print("已启动。请在浏览器中播放 Bilibili 视频，识别结果将实时显示在此。")
    print("按 Ctrl+C 停止。\n")

    while not _stop.is_set():
        try:
            await stt_session(audio_q)
        except (websockets.exceptions.ConnectionClosed, ConnectionError) as e:
            if not _stop.is_set():
                print(f"\n[重连] 连接断开 ({e})，3 秒后重试...")
                await asyncio.sleep(3)
        except Exception as e:
            if not _stop.is_set():
                print(f"\n[错误] {type(e).__name__}: {e}，3 秒后重试...")
                await asyncio.sleep(3)

    print("\n已停止。")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        _stop.set()
        sys.exit(0)


if __name__ == "__main__":
    main()
