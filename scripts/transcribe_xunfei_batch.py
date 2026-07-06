"""并发转写多个 WAV 文件（异步，同时建立多个 WebSocket 连接）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 把 scripts 的父目录加入 path，使得 transcribe_xunfei 可被直接 import
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.transcribe_xunfei import transcribe


async def main() -> None:
    base = Path("recordings/991982a1-aa5f-4b23-b77f-ac5ede44b472")

    # 免费账号并发路数=1，顺序执行
    print(">>> [1/2] 开始转写候选人音频...")
    await transcribe(
        base / "full_candidate.wav",
        base / "full_candidate.xunfei.txt",
        speed=2.0,
    )
    print("\n>>> [2/2] 开始转写面试官音频...")
    await transcribe(
        base / "full_interviewer.wav",
        base / "full_interviewer.xunfei.txt",
        speed=2.0,
    )
    print("\n两路转写全部完成。")


if __name__ == "__main__":
    asyncio.run(main())
