from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .candidate import CandidateProfile


class InterviewStage(str, Enum):
    """面试会话的生命周期阶段"""
    IDLE = "idle"
    RESUME_ANALYSIS = "resume_analysis"
    INTERVIEWING = "interviewing"
    EVALUATING = "evaluating"
    COMPLETED = "completed"


@dataclass
class InterviewQuestion:
    id: int
    dimension: str                         # "系统设计" | "算法" | "项目经验" | ...
    question: str
    follow_ups: list[str]
    difficulty: str = "medium"             # "easy" | "medium" | "hard"
    source: str = "auto"                   # "auto" | "manual"
    is_covered: bool = False


@dataclass
class ConversationRound:
    round_number: int
    interviewer_text: str
    candidate_text: str
    llm_suggestion: str | None = None
    interviewer_audio_path: str | None = None
    candidate_audio_path: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class SessionMetadata:
    candidate_id: str
    start_time: datetime
    end_time: datetime | None = None
    trigger_mode: str = "auto"             # "auto" | "manual"
    total_rounds: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0


@dataclass
class InterviewSession:
    id: str
    candidate: CandidateProfile
    question_plan: list[InterviewQuestion]
    rounds: list[ConversationRound]
    stage: InterviewStage
    context_summary: str
    covered_dimensions: set[str]
    working_notes: str
    metadata: SessionMetadata


@dataclass
class TokenUsageInfo:
    total_used: int
    budget: int
    fixed_zone_tokens: int
    summary_zone_tokens: int
    window_zone_tokens: int
    is_compressing: bool
    utilization: float                     # 0.0 - 1.0
