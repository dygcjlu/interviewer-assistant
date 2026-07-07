"""Integration test: build_report_pdf — generate-then-reread roundtrip with Chinese text.

Verifies that CJK characters render correctly in the exported PDF (no garbled/replacement
characters) on Windows where SimHei/MSYaHei/SimSun fonts are available. On non-Windows CI
environments lacking CJK fonts, the test skips gracefully.
"""

from __future__ import annotations

from datetime import datetime

import pytest

fitz = pytest.importorskip("fitz")  # pymupdf; installed as a production dep (requirements.txt)

from src.models.evaluation import DimensionScore, EvalReport  # noqa: E402
from src.utils.pdf_export import _ensure_cjk_font, build_report_pdf  # noqa: E402


def _sample_report() -> EvalReport:
    return EvalReport(
        id="er-test",
        interview_id="iv-test",
        dimensions=[
            DimensionScore(
                dimension="技术深度",
                score=8.0,
                comment="扎实",
                evidence=["答对分布式一致性"],
            )
        ],
        overall_score=8.0,
        strengths=["沟通清晰"],
        weaknesses=["系统设计经验不足"],
        recommendation="hire",
        summary="综合表现良好，推荐进入下一轮。",
        generated_at=datetime(2026, 7, 6, 10, 30),
        candidate_id="c-test",
    )


@pytest.mark.integration
def test_build_report_pdf_roundtrip_chinese():
    """PDF 生成→回读：验证中文字符正确渲染，无乱码/替换字符。"""
    pdf_bytes = build_report_pdf(_sample_report(), candidate_name="张三")

    # Basic structural check — valid PDF header
    assert pdf_bytes[:4] == b"%PDF"

    # Re-read with pymupdf and extract all text
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    doc.close()

    # _ensure_cjk_font() returns the cached font name used during PDF generation.
    # "Helvetica" means no CJK font was found (non-Windows CI), so we skip CJK assertions.
    has_cjk_font = _ensure_cjk_font() != "Helvetica"
    if has_cjk_font:
        assert "面试评价报告" in text
        assert "张三" in text
        assert "技术深度" in text
        # No replacement characters — CJK glyphs must have been found in the registered font
        assert "\ufffd" not in text
    else:
        pytest.skip("无可用 CJK 字体（非 Windows CI），跳过中文断言")
