"""UserMemoryStore — 面试官偏好记忆文件（USER.md）的条目化管理。

条目之间以 ENTRY_DELIMITER 分隔，支持精确的 add / replace / remove 操作，
并使用原子写入（mkstemp + os.replace）保证文件安全。
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..utils import write_atomic

logger = logging.getLogger(__name__)

ENTRY_DELIMITER = "\n\n---\n\n"
DEFAULT_CHAR_LIMIT = 3000


class UserMemoryStore:
    """持久化面试官偏好的条目列表，映射到 USER.md 文件。

    每个条目是一段自由格式文本。条目以 ENTRY_DELIMITER 分隔存储，
    LLM 可通过索引精确替换或删除某条，而不必重写全文。
    """

    def __init__(self, path: Path, char_limit: int = DEFAULT_CHAR_LIMIT) -> None:
        self._path = path
        self.char_limit = char_limit
        self._entries: list[str] = []

    # ── 读取 ──────────────────────────────────────────────────────────────────

    def load(self) -> None:
        """从磁盘读取 USER.md，解析为条目列表。

        向后兼容：若文件不含 ENTRY_DELIMITER，整个文件作为 entries[0]。
        """
        if not self._path.exists():
            self._entries = []
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("UserMemoryStore: failed to read %s", self._path)
            self._entries = []
            return

        if not raw.strip():
            self._entries = []
            return

        if ENTRY_DELIMITER in raw:
            self._entries = [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]
        else:
            # 旧格式：整个文件视为单条目
            self._entries = [raw.strip()]

        logger.debug(
            "UserMemoryStore: loaded %d entries (%d chars) from %s",
            len(self._entries),
            len(raw),
            self._path,
        )

    # ── 渲染 ──────────────────────────────────────────────────────────────────

    def render(self) -> str:
        """返回完整文本，供注入 system prompt。"""
        return ENTRY_DELIMITER.join(self._entries)

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def list_entries(self) -> list[dict]:
        """返回条目列表，每项含 index 和 content，供 LLM 选择操作对象。"""
        return [{"index": i, "content": e} for i, e in enumerate(self._entries)]

    def __len__(self) -> int:
        return len(self._entries)

    # ── 写操作 ────────────────────────────────────────────────────────────────

    def add(self, content: str) -> int:
        """追加新条目，返回新条目的索引。超出字符上限时抛出 ValueError。"""
        content = content.strip()
        if not content:
            raise ValueError("条目内容不能为空")
        # M4-5: 内容含分隔符时写入后 load() 会被错误拆分为多条
        if ENTRY_DELIMITER in content:
            raise ValueError(
                f"条目内容不能包含分隔符（{ENTRY_DELIMITER!r}），请检查内容是否异常"
            )
        new_render = (
            self.render() + (ENTRY_DELIMITER if self._entries else "") + content
        )
        if len(new_render) > self.char_limit:
            raise ValueError(
                f"添加后总字符数 ({len(new_render)}) 超过上限 ({self.char_limit})，"
                "请先删除旧条目"
            )
        self._entries.append(content)
        self._write_atomic()
        return len(self._entries) - 1

    def replace(self, index: int, content: str) -> None:
        """替换指定索引的条目。"""
        self._check_index(index)
        content = content.strip()
        if not content:
            raise ValueError("替换内容不能为空")
        # M4-5: 与 add() 保持一致，防止含分隔符的内容破坏 load() 解析
        if ENTRY_DELIMITER in content:
            raise ValueError(
                f"条目内容不能包含分隔符（{ENTRY_DELIMITER!r}），请检查内容是否异常"
            )
        old = self._entries[index]
        self._entries[index] = content
        new_render = self.render()
        if len(new_render) > self.char_limit:
            self._entries[index] = old  # 回滚
            raise ValueError(
                f"替换后总字符数 ({len(new_render)}) 超过上限 ({self.char_limit})"
            )
        self._write_atomic()

    def remove(self, index: int) -> None:
        """删除指定索引的条目。"""
        self._check_index(index)
        del self._entries[index]
        self._write_atomic()

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _check_index(self, index: int) -> None:
        if not self._entries:
            raise IndexError("记忆列表为空，无条目可操作")
        if index < 0 or index >= len(self._entries):
            raise IndexError(
                f"索引 {index} 超出范围（当前共 {len(self._entries)} 条，索引 0~{len(self._entries)-1}）"
            )

    def _write_atomic(self) -> None:
        """原子写入 USER.md：委托公共 write_atomic 工具。"""
        content = self.render()
        write_atomic(self._path, content)
        logger.debug(
            "UserMemoryStore: wrote %d entries (%d chars) to %s",
            len(self._entries),
            len(content),
            self._path,
        )
