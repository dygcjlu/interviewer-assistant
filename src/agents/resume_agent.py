"""ResumeAgent — 任务驱动的 ReAct 循环，负责简历解析与面试题目生成。"""
from __future__ import annotations

import json
import logging

from .base import AgentRequest, AgentResponse, BaseAgent
from ..models.message import Message
from ..models.session import InterviewSession

logger = logging.getLogger(__name__)


class ResumeAgent(BaseAgent):
    """简历分析 Agent — ReAct 模式，通过工具自主完成任务。"""

    async def on_activate(self, session: InterviewSession) -> None:
        logger.info("ResumeAgent activated for session %s", session.id)

    async def on_deactivate(self, session: InterviewSession) -> None:
        logger.info("ResumeAgent deactivated for session %s", session.id)

    async def execute(self, task: str) -> dict:
        """ReAct 入口 — 由 dispatch_to_agent 工具调用。

        Args:
            task: 自然语言任务描述，例如：
                  "将 resumes/张三.pdf 解析为 Markdown 并保存为 resumes/张三.md"

        Returns:
            {"type": "parse_done", ...} | {"type": "questions_done", ...} | {"type": "error", ...}
        """
        from ..config import get_settings
        settings = get_settings()
        max_rounds = settings.RESUME_AGENT_MAX_TOOL_ROUNDS

        messages = self._build_messages(task)
        try:
            result_text = await self._run_with_tools(messages, max_tool_rounds=max_rounds)
            return _extract_json(result_text)
        except Exception as exc:
            logger.exception("ResumeAgent.execute failed task=%r", task)
            return {"type": "error", "message": str(exc)}

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        """兼容 BaseAgent 接口（不对外使用）。"""
        return AgentResponse(success=False, error="ResumeAgent 只通过 dispatch_to_agent 调用")

    def _build_messages(self, task: str) -> list[Message]:
        from ..framework.prompt_builder import AgentConfig
        from ..models.session import InterviewSession, InterviewStage, SessionMetadata
        from ..models.candidate import CandidateProfile
        import uuid
        from datetime import datetime

        dummy_session = InterviewSession(
            id=str(uuid.uuid4()),
            candidate=CandidateProfile(id=str(uuid.uuid4()), name=""),
            question_plan=[],
            rounds=[],
            stage=InterviewStage.IDLE,
            context_summary="",
            covered_dimensions=set(),
            working_notes="",
            metadata=SessionMetadata(candidate_id="", start_time=datetime.now()),
        )
        messages = self.prompt_builder.build(dummy_session, self.config)
        messages.append(Message(role="user", content=task))
        return messages


def _normalize_questions(data: dict | list) -> list[dict]:
    """将 LLM 输出规范为题目 dict 列表。"""
    if isinstance(data, dict):
        raw = data.get("questions", data.get("题目", []))
        if not isinstance(raw, list):
            return []
        data = raw
    if not isinstance(data, list):
        return []
    normalized: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        question = (
            item.get("question")
            or item.get("题目")
            or item.get("content")
            or ""
        )
        if not str(question).strip():
            continue
        follow_ups = item.get("follow_ups") or item.get("追问") or []
        if isinstance(follow_ups, str):
            follow_ups = [follow_ups]
        normalized.append(
            {
                "dimension": item.get("dimension") or item.get("维度") or "通用",
                "question": str(question),
                "follow_ups": [str(f) for f in follow_ups if f],
                "difficulty": item.get("difficulty") or item.get("难度") or "medium",
            }
        )
    return normalized


def _extract_json(text: str) -> dict | list:
    """从 LLM 输出中尽力提取 JSON。容错处理 ```json 代码块包裹。"""
    if not text:
        raise json.JSONDecodeError("empty response", "", 0)
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    start_obj = text.find("{")
    start_arr = text.find("[")
    candidates = [c for c in (start_obj, start_arr) if c >= 0]
    if not candidates:
        raise json.JSONDecodeError("no JSON object found", text, 0)
    start = min(candidates)
    try:
        obj, _ = decoder.raw_decode(text, start)
        return obj
    except json.JSONDecodeError:
        end_obj = text.rfind("}")
        end_arr = text.rfind("]")
        end = max(end_obj, end_arr)
        if end > start:
            return json.loads(text[start : end + 1])
        raise json.JSONDecodeError("no valid JSON found", text, start)
