from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class DimensionScore:
    dimension: str
    score: float                           # 1-10
    comment: str
    evidence: list[str]                    # 候选人原话引用


@dataclass
class EvalReport:
    id: str
    interview_id: str
    dimensions: list[DimensionScore]
    overall_score: float                   # 1-10
    strengths: list[str]
    weaknesses: list[str]
    recommendation: str                    # "strong_hire" | "hire" | "weak_hire" | "no_hire"
    summary: str
    generated_at: datetime
    candidate_id: str = ""
    question_coverage: str = ""
