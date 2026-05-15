"""parse_resume_pdf — 从 PDF 文件提取文本，供 LLM 处理。"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def _extract_pdf_text(file_path: str) -> dict:
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    doc = fitz.open(str(path))
    page_count = len(doc)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return {"text": text, "pages": page_count}


async def parse_resume_pdf(file_path: str) -> str:
    """从 PDF 文件提取文本，返回 JSON 字符串供 LLM 处理。"""
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _extract_pdf_text, file_path)
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        logger.exception("parse_resume_pdf: failed to parse %s", file_path)
        return json.dumps({"error": str(exc)})