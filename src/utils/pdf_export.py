"""面试评价报告 PDF 导出工具（使用 reportlab）。"""

from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models.evaluation import EvalReport

_FONT_REGISTERED = False
_FONT_NAME = "Helvetica"  # fallback


def _ensure_cjk_font() -> str:
    """尝试注册系统 CJK 字体，返回可用字体名。"""
    global _FONT_REGISTERED, _FONT_NAME
    if _FONT_REGISTERED:
        return _FONT_NAME
    candidates = [
        ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
        ("C:/Windows/Fonts/msyh.ttc", "MicrosoftYaHei"),
        ("C:/Windows/Fonts/simsun.ttc", "SimSun"),
    ]
    for path, name in candidates:
        try:
            pdfmetrics.registerFont(TTFont(name, path))
            _FONT_NAME = name
            _FONT_REGISTERED = True
            return _FONT_NAME
        except Exception:
            continue
    _FONT_REGISTERED = True
    return _FONT_NAME


_RECOMMENDATION_LABELS = {
    "strong_hire": "强烈推荐录用",
    "hire": "推荐录用",
    "weak_hire": "谨慎录用",
    "no_hire": "不推荐录用",
}


def build_report_pdf(report: EvalReport, candidate_name: str = "") -> bytes:
    """将 EvalReport 渲染为 PDF 字节流。"""
    font = _ensure_cjk_font()
    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", fontName=font, fontSize=18, leading=24, spaceAfter=6)
    h2 = ParagraphStyle(
        "h2",
        fontName=font,
        fontSize=13,
        leading=18,
        spaceBefore=10,
        spaceAfter=4,
        textColor=colors.HexColor("#1a56db"),
    )
    body = ParagraphStyle("body", fontName=font, fontSize=10, leading=15)
    small = ParagraphStyle(
        "small", fontName=font, fontSize=9, leading=13, textColor=colors.grey
    )
    label = ParagraphStyle(
        "label", fontName=font, fontSize=10, leading=14, fontWeight="bold"
    )

    rec = _RECOMMENDATION_LABELS.get(report.recommendation, report.recommendation)
    rec_color = (
        colors.HexColor("#15803d")
        if "hire" in report.recommendation
        else colors.HexColor("#b91c1c")
    )

    story = []

    # ── 标题 ──────────────────────────────────────────────────────────────────
    title = "面试评价报告"
    if candidate_name:
        title += f" — {candidate_name}"
    story.append(Paragraph(title, h1))
    story.append(
        Paragraph(
            f"生成时间：{report.generated_at.strftime('%Y-%m-%d %H:%M')}　｜　"
            f"面试 ID：{report.interview_id}　｜　"
            f"综合评分：{report.overall_score:.1f} / 10",
            small,
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e5e7eb"))
    )
    story.append(Spacer(1, 4 * mm))

    # ── 录用建议 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("录用建议", h2))
    rec_style = ParagraphStyle(
        "rec", fontName=font, fontSize=13, leading=18, textColor=rec_color
    )
    story.append(Paragraph(rec, rec_style))
    story.append(Spacer(1, 4 * mm))

    # ── 总结 ──────────────────────────────────────────────────────────────────
    story.append(Paragraph("总结", h2))
    story.append(Paragraph(report.summary or "—", body))
    story.append(Spacer(1, 4 * mm))

    # ── 维度评分 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("维度评分", h2))
    table_data = [
        [
            Paragraph("维度", label),
            Paragraph("评分", label),
            Paragraph("评语", label),
            Paragraph("证据引用", label),
        ]
    ]
    for dim in report.dimensions:
        evidence_text = (
            "\n".join(f"· {e}" for e in dim.evidence) if dim.evidence else "—"
        )
        table_data.append(
            [
                Paragraph(dim.dimension, body),
                Paragraph(f"{dim.score:.1f}", body),
                Paragraph(dim.comment or "—", body),
                Paragraph(evidence_text, small),
            ]
        )
    col_widths = [35 * mm, 18 * mm, 60 * mm, 57 * mm]
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f9fafb")],
                ),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 4 * mm))

    # ── 优势 / 劣势 ───────────────────────────────────────────────────────────
    story.append(Paragraph("优势", h2))
    for s in report.strengths:
        story.append(Paragraph(f"• {s}", body))
    story.append(Spacer(1, 3 * mm))

    story.append(Paragraph("劣势 / 待提升", h2))
    for w in report.weaknesses:
        story.append(Paragraph(f"• {w}", body))

    doc.build(story)
    return buf.getvalue()
