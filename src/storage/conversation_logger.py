"""ConversationLogger — 将 Agent 与 LLM 之间的完整消息列表持久化到 JSONL 文件。

每行一个 JSON 对象，格式：
  {"role": "...", "content": "...", "timestamp": "2026-05-20T12:00:00"}
文件以 UTF-8 追加方式写入，通过 asyncio.to_thread 避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from datetime import datetime
from pathlib import Path

from ..models.message import Message

logger = logging.getLogger(__name__)


def _msg_to_dict(msg: Message) -> dict:
    """将 Message dataclass 序列化为 JSONL 行字典，排除 None 字段，附加写入时间戳。"""
    d: dict = {"role": msg.role}
    if msg.content is not None:
        d["content"] = msg.content
    if msg.tool_calls is not None:
        d["tool_calls"] = [dataclasses.asdict(tc) for tc in msg.tool_calls]
    if msg.tool_call_id is not None:
        d["tool_call_id"] = msg.tool_call_id
    d["timestamp"] = datetime.now().isoformat(timespec="seconds")
    return d


class ConversationLogger:
    """追加写入 JSONL，记录单个 Agent 与 LLM 的完整对话消息列表。"""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._last_system_content: str | None = None

    async def append(self, messages: list[Message]) -> None:
        """追加写入，内部用 asyncio.to_thread 避免阻塞事件循环。"""
        if not messages:
            return
        await asyncio.to_thread(self._sync_write, messages)

    def _sync_write(self, messages: list[Message]) -> None:
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                for msg in messages:
                    line = json.dumps(_msg_to_dict(msg), ensure_ascii=False)
                    f.write(line + "\n")
        except OSError:
            logger.exception("ConversationLogger: write failed to %s", self._path)

    async def append_with_system(
        self, system_content: str, messages: list[Message]
    ) -> None:
        """若 system prompt 变更（或首次），先追加一条 system 行，再追加其余消息。"""
        to_write: list[Message] = []
        if system_content != self._last_system_content:
            to_write.append(Message(role="system", content=system_content))
            self._last_system_content = system_content
        to_write.extend(messages)
        await self.append(to_write)
