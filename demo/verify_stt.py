"""验证百度实时语音识别 WebSocket 连接是否正常。

模拟实时发送 3 秒静音 PCM（每帧间隔 160ms），并发接收响应。
- 连接成功 + 无鉴权错误 → 验证通过
- err_no=-3004 (authentication failed) → 凭证有误

运行方式：
    python -m demo.verify_stt
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid

import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

APPID = 121087443
APPKEY = "eLpyLuxR0of5RWsn497uSdp0"
DEV_PID = 15372
SILENCE_SECONDS = 3
BAIDU_CHUNK_BYTES = 5120        # 160ms at 16kHz 16-bit mono
FRAME_INTERVAL_S = 0.16         # 模拟实时发送间隔


async def verify() -> bool:
    print("=" * 60)
    print("百度实时语音识别 – 连接验证")
    print("=" * 60)

    sn = str(uuid.uuid4())
    url = f"wss://vop.baidu.com/realtime_asr?sn={sn}"

    print(f"[1/4] 连接 WebSocket ...")
    ws = await websockets.connect(url)
    print("[1/4] ✓ WebSocket 连接成功")

    start_frame = {
        "type": "START",
        "data": {
            "appid": APPID,
            "appkey": APPKEY,
            "dev_pid": DEV_PID,
            "cuid": "verify-test-001",
            "format": "pcm",
            "sample": 16000,
        },
    }
    await ws.send(json.dumps(start_frame))
    print("[1/4] ✓ START 帧已发送")

    messages = []
    auth_error = False
    receive_done = asyncio.Event()

    async def receive_loop():
        nonlocal auth_error
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                messages.append(msg)
                msg_type = msg.get("type", "?")
                err_no = msg.get("err_no", 0)
                result = msg.get("result", "")
                print(
                    f"    <- type={msg_type} err_no={err_no}"
                    + (f' result="{result}"' if result else "")
                )
                # 鉴权失败错误码
                if msg_type == "FIN_TEXT" and err_no in (-3004, -3005):
                    auth_error = True
                if msg_type == "FIN_TEXT":
                    break
        except (websockets.exceptions.ConnectionClosed, Exception) as e:
            print(f"    (receive_loop 结束: {type(e).__name__}: {e})")
        finally:
            receive_done.set()

    recv_task = asyncio.ensure_future(receive_loop())

    total_bytes = 16000 * 2 * SILENCE_SECONDS
    silence = bytes(total_bytes)
    print(
        f"[2/4] 实时发送 {SILENCE_SECONDS}s 静音音频（每帧 {FRAME_INTERVAL_S*1000:.0f}ms 间隔）..."
    )
    offset = 0
    chunks_sent = 0
    while offset < len(silence):
        chunk = silence[offset : offset + BAIDU_CHUNK_BYTES]
        await ws.send(chunk)
        offset += BAIDU_CHUNK_BYTES
        chunks_sent += 1
        await asyncio.sleep(FRAME_INTERVAL_S)
    print(f"[2/4] ✓ 发送完成（{chunks_sent} 帧）")

    print("[3/4] 发送 FINISH 帧，等待服务端响应（最多 10s）...")
    await ws.send(json.dumps({"type": "FINISH"}))

    try:
        await asyncio.wait_for(receive_done.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        print("    (等待 10s 超时，继续)")

    recv_task.cancel()
    try:
        await ws.close()
    except Exception:
        pass

    print()
    print("[4/4] 验证结果:")
    if auth_error:
        err = next(
            (m for m in messages if m.get("err_no") in (-3004, -3005)), {}
        )
        print(
            f"    ✗ 鉴权失败: err_no={err.get('err_no')} "
            f"err_msg={err.get('err_msg')}"
        )
        return False
    elif not messages:
        print("    ? 未收到任何响应（服务端静默关闭，可能是静音被忽略，但无鉴权错误）")
        # 连接本身成功，静音未触发识别属于正常行为
        return True
    else:
        has_error = any(m.get("err_no", 0) != 0 for m in messages)
        if has_error:
            errs = [m for m in messages if m.get("err_no", 0) != 0]
            print(f"    ✗ 收到错误帧: {errs}")
            return False
        print(f"    ✓ 连接正常，收到 {len(messages)} 条消息，无错误")
        return True


def main() -> None:
    loop = asyncio.get_event_loop()
    ok = loop.run_until_complete(verify())
    print()
    if ok:
        print("结论：百度 STT 配置验证通过 ✓")
    else:
        print("结论：验证失败，请检查 APPID / APPKEY 是否正确")
        sys.exit(1)


if __name__ == "__main__":
    main()
