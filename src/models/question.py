"""结构化面试问题模型。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InterviewQuestion:
    id: str
    question: str
    focus: str                    # 预期考察点
    covered: bool = False
    covered_by: str = ""          # "auto" | "manual" | ""
