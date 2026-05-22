"""parse_resume_pdf — 从 PDF 提取 Markdown 文本（纯函数，无外部依赖）。"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pymupdf4llm

logger = logging.getLogger(__name__)

DESCRIPTION = "解析候选人简历 PDF 文件，提取 Markdown 格式文本内容"
SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string", "description": "PDF 简历文件路径"},
    },
    "required": ["file_path"],
}


async def parse_resume_pdf(file_path: str) -> str:
    """从 PDF 文件提取结构化 Markdown，返回 JSON 字符串。"""
    def _extract(path: str) -> dict:
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        return {"text": pymupdf4llm.to_markdown(str(p)), "pages": None}

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _extract, file_path)
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        logger.exception("parse_resume_pdf: failed to parse %s", file_path)
        return json.dumps({"error": str(exc)})
