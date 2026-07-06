"""ScriptPlayer — 按预定义脚本向 TranscriptionManager 注入对话片段，用于调试。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from .protocol import TranscriptSegment
from .transcription import TranscriptionManager

logger = logging.getLogger(__name__)


class ScriptPlayer:
    """读取 JSON 脚本文件，按时序向 TranscriptionManager 注入 TranscriptSegment。

    脚本格式（JSON 数组）::

        [
          {"source": "interviewer", "text": "...", "delay_ms": 500},
          {"source": "candidate",   "text": "...", "delay_ms": 2000},
          ...
        ]

    ``delay_ms`` 为距上一条消息的等待时间（毫秒）。
    所有片段均以 ``is_final=True`` 注入，不模拟中间结果。
    """

    def __init__(self, script_path: str) -> None:
        self._script_path = Path(script_path)
        self._task: asyncio.Task | None = None

    async def start(self, transcription_manager: TranscriptionManager) -> None:
        """加载脚本并启动后台回放任务。"""
        script = self._load_script()
        self._task = asyncio.create_task(self._play(script, transcription_manager))
        logger.info(
            "ScriptPlayer: started, %d entries from %s", len(script), self._script_path
        )

    async def stop(self) -> None:
        """取消回放任务（若仍在运行）。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("ScriptPlayer: stopped")

    # ── internals ─────────────────────────────────────────────────────────────

    def _load_script(self) -> list[dict]:
        with self._script_path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError(
                f"ScriptPlayer: script must be a JSON array, got {type(data)}"
            )
        return data

    async def _play(self, script: list[dict], tm: TranscriptionManager) -> None:
        try:
            for entry in script:
                delay_sec = entry.get("delay_ms", 1000) / 1000.0
                await asyncio.sleep(delay_sec)

                source = entry["source"]
                text = entry["text"]
                segment = TranscriptSegment(
                    text=text,
                    source=source,
                    is_final=True,
                    timestamp=datetime.now(),
                )
                logger.debug("ScriptPlayer: injecting [%s] %s", source, text[:40])
                await tm.on_segment(segment)

            logger.info("ScriptPlayer: script playback complete")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("ScriptPlayer: playback error")
