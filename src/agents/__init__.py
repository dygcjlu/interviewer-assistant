"""agents 包入口 — 暴露 MainAgent、InterviewController、三个 Agent 与共享类型。"""

from .base import AgentRequest, AgentResponse, BaseAgent
from .eval_agent import EvalAgent
from .interview_agent import InterviewAgent
from .interview_controller import InterviewController
from .main_agent import MainAgent
from .prompts import (
    EVAL_AGENT_SYSTEM_PROMPT,
    INTERVIEW_AGENT_SYSTEM_PROMPT,
    RESUME_AGENT_SYSTEM_PROMPT,
)
from .resume_agent import ResumeAgent

__all__ = [
    "AgentRequest",
    "AgentResponse",
    "BaseAgent",
    "EvalAgent",
    "EVAL_AGENT_SYSTEM_PROMPT",
    "INTERVIEW_AGENT_SYSTEM_PROMPT",
    "InterviewAgent",
    "InterviewController",
    "MainAgent",
    "RESUME_AGENT_SYSTEM_PROMPT",
    "ResumeAgent",
]
