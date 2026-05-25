"""QwenVLParser — 使用 Qwen-VL 多模态 LLM 逐页识别 PDF。"""
from __future__ import annotations

import asyncio
import base64
import io
import logging

import openai
import pymupdf  # type: ignore

from src.config import get_settings

from .base import BasePDFParser

logger = logging.getLogger(__name__)

_PROMPT = (
    "请将图片中的文档内容完整转换为 Markdown 格式。"
    "保留所有标题层级、列表结构和段落划分，不要添加任何额外解释或注释，直接输出 Markdown 文本。"
)
_DPI = 150


class QwenVLParser(BasePDFParser):
    async def extract(self, file_path: str) -> str:
        settings = get_settings()
        client = openai.AsyncOpenAI(
            api_key=settings.QWEN_API_KEY,
            base_url=settings.QWEN_API_BASE_URL,
        )

        pages_b64 = await asyncio.get_running_loop().run_in_executor(
            None, self._render_pages, file_path
        )
        logger.info("QwenVLParser: rendered %d pages from %s", len(pages_b64), file_path)

        tasks = [
            self._extract_page(client, settings.QWEN_VL_MODEL, b64)
            for b64 in pages_b64
        ]
        results = await asyncio.gather(*tasks)
        return "\n\n".join(results)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _render_pages(file_path: str) -> list[str]:
        """将 PDF 每页渲染为 PNG，返回 base64 字符串列表。"""
        pages: list[str] = []
        mat = pymupdf.Matrix(_DPI / 72, _DPI / 72)
        with pymupdf.open(file_path) as doc:
            for page in doc:
                pix = page.get_pixmap(matrix=mat)
                buf = io.BytesIO()
                buf.write(pix.tobytes("png"))
                pages.append(base64.b64encode(buf.getvalue()).decode())
        return pages

    @staticmethod
    async def _extract_page(
        client: openai.AsyncOpenAI, model: str, b64: str
    ) -> str:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                        {"type": "text", "text": _PROMPT},
                    ],
                }
            ],
        )
        return response.choices[0].message.content or ""
