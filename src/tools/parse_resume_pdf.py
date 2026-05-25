"""parse_resume_pdf — 从 PDF 提取 Markdown 文本（Strategy 模式，支持多种解析引擎）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import get_settings
from src.tools.pdf_parsers import BasePDFParser, MineruParser, PymupdfParser, QwenVLParser

logger = logging.getLogger(__name__)

DESCRIPTION = "解析候选人简历 PDF 文件，提取 Markdown 格式文本内容"
SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string", "description": "PDF 简历文件路径"},
    },
    "required": ["file_path"],
}


def get_pdf_parser(parser_type: str) -> BasePDFParser:
    if parser_type == "qwen_vl":
        return QwenVLParser()
    elif parser_type == "mineru":
        return MineruParser()
    else:
        return PymupdfParser()


async def parse_resume_pdf(file_path: str) -> str:
    """从 PDF 文件提取结构化 Markdown，返回 JSON 字符串。"""
    if not Path(file_path).exists():
        return json.dumps({"error": f"File not found: {file_path}"})

    settings = get_settings()
    parser = get_pdf_parser(settings.PDF_PARSER)
    logger.info("parse_resume_pdf: using parser=%s for %s", settings.PDF_PARSER, file_path)

    try:
        text = await parser.extract(file_path)
        return json.dumps({"text": text, "pages": None}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("parse_resume_pdf failed: %s", file_path)
        return json.dumps({"error": str(exc)})
