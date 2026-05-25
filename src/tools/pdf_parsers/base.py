"""BasePDFParser — 所有 PDF 解析器的抽象基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BasePDFParser(ABC):
    @abstractmethod
    async def extract(self, file_path: str) -> str:
        """从 PDF 提取 Markdown 文本。"""
