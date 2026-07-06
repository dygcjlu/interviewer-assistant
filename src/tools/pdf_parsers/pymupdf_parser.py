"""PymupdfParser — 使用 pymupdf4llm 的本地解析器（原有逻辑）。"""

from __future__ import annotations

import asyncio

import pymupdf4llm

from .base import BasePDFParser


class PymupdfParser(BasePDFParser):
    async def extract(self, file_path: str) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: pymupdf4llm.to_markdown(file_path)
        )
