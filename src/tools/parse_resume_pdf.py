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
    """从 PDF 文件提取结构化 Markdown，返回 JSON 字符串。

    L1-6: 失败时返回 user_facing=True，让上层（ResumeAgent / MainAgent）跳过 LLM 自由发挥，
          将原始错误直接透传给用户，便于诊断（限流？文件损坏？网络？）。
    S-12: 主 parser（qwen_vl / mineru）失败时自动降级到 pymupdf 兜底；降级时附 warning。
    """
    if not Path(file_path).exists():
        return json.dumps(
            {"error": f"PDF 文件不存在：{file_path}", "user_facing": True},
            ensure_ascii=False,
        )

    settings = get_settings()
    primary_type = settings.PDF_PARSER
    parser = get_pdf_parser(primary_type)
    logger.info("parse_resume_pdf: using parser=%s for %s", primary_type, file_path)

    try:
        text = await parser.extract(file_path)
        return json.dumps({"text": text, "pages": None}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("parse_resume_pdf primary parser failed: %s", file_path)
        primary_err_msg = f"{exc.__class__.__name__}: {str(exc)[:200]}"

        # S-12: 主 parser 已是 pymupdf 时无降级路径，直接报错
        if primary_type == "pymupdf":
            return json.dumps(
                {"error": f"解析 PDF 失败：{primary_err_msg}", "user_facing": True},
                ensure_ascii=False,
            )

        # 自动降级到 pymupdf
        try:
            fallback = get_pdf_parser("pymupdf")
            logger.warning(
                "parse_resume_pdf: primary=%s failed, falling back to pymupdf for %s",
                primary_type, file_path,
            )
            text = await fallback.extract(file_path)
            return json.dumps(
                {
                    "text": text,
                    "pages": None,
                    "warning": (
                        f"主解析器 {primary_type} 失败（{primary_err_msg}），"
                        f"已降级为 pymupdf 兜底。文字内容可能较粗糙，请人工核对。"
                    ),
                },
                ensure_ascii=False,
            )
        except Exception as fb_exc:
            logger.exception("parse_resume_pdf: pymupdf fallback also failed: %s", file_path)
            return json.dumps(
                {
                    "error": (
                        f"解析 PDF 失败：主解析器 {primary_type} 报错 [{primary_err_msg}]；"
                        f"pymupdf 降级也失败 [{fb_exc.__class__.__name__}: {str(fb_exc)[:120]}]"
                    ),
                    "user_facing": True,
                },
                ensure_ascii=False,
            )
