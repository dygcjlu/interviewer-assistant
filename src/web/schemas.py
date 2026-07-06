"""Web 层 Pydantic 请求/响应 schema。"""

from __future__ import annotations

from pydantic import BaseModel


class StartInterviewRequest(BaseModel):
    candidate_id: str
    trigger_mode: str = "auto"


class SwitchAgentRequest(BaseModel):
    target_agent: str


class ChatRequest(BaseModel):
    message: str


class CandidateSelectRequest(BaseModel):
    candidate_id: str
