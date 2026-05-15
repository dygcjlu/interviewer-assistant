"""ResumeAgent — 简历解析与面试题目生成。"""
from __future__ import annotations

import json
import logging

from .base import AgentRequest, AgentResponse, BaseAgent
from ..models.candidate import CandidateProfile, Education, ProjectExperience, WorkExperience
from ..models.message import Message
from ..models.session import InterviewSession

logger = logging.getLogger(__name__)


class ResumeAgent(BaseAgent):
    """简历分析 Agent — 解析 PDF、生成题目清单。"""

    async def on_activate(self, session: InterviewSession) -> None:
        logger.info("ResumeAgent activated for session %s", session.id)

    async def on_deactivate(self, session: InterviewSession) -> None:
        logger.info("ResumeAgent deactivated for session %s", session.id)

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        if request.type == "parse_resume":
            return await self._parse_resume(request)
        if request.type == "generate_questions":
            return await self._generate_questions(request)
        return AgentResponse(
            success=False, error=f"Unknown request type: {request.type!r}"
        )

    # ── internals ─────────────────────────────────────────────────────────────

    async def _parse_resume(self, request: AgentRequest) -> AgentResponse:
        file_path = request.payload.get("file_path", "")
        if not file_path:
            return AgentResponse(success=False, error="缺少必填参数 file_path")

        messages = self.prompt_builder.build(request.session, self.config)
        messages.append(
            Message(
                role="user",
                content=(
                    f"请解析候选人简历文件：{file_path}\n"
                    "请调用 parse_resume_pdf 工具读取 PDF 内容，"
                    "然后以 JSON 格式输出包含 name/email/phone/education/work_experience/"
                    "skills/projects/resume_summary 字段的候选人结构化信息。"
                ),
            )
        )

        try:
            result_text = await self._run_with_tools(messages)
            data = _extract_json(result_text)
            _update_candidate_from_data(request.session.candidate, data)
            return AgentResponse(
                success=True,
                data={"profile_data": data, "file_path": file_path},
            )
        except Exception as exc:
            logger.exception("ResumeAgent: parse_resume failed")
            return AgentResponse(success=False, error=str(exc))

    async def _generate_questions(self, request: AgentRequest) -> AgentResponse:
        messages = self.prompt_builder.build(request.session, self.config)
        messages.append(
            Message(
                role="user",
                content=(
                    "请根据候选人简历生成 8-12 道面试题目清单，"
                    "每道题目包含 dimension、question、follow_ups (2-3 个)、"
                    "difficulty (easy/medium/hard) 字段，以 JSON 数组格式输出。"
                ),
            )
        )

        try:
            result_text = await self._run_with_tools(messages)
            questions_data = _extract_json(result_text)
            if not isinstance(questions_data, list):
                questions_data = questions_data.get("questions", []) if isinstance(
                    questions_data, dict
                ) else []
            return AgentResponse(success=True, data={"questions": questions_data})
        except Exception as exc:
            logger.exception("ResumeAgent: generate_questions failed")
            return AgentResponse(success=False, error=str(exc))


def _update_candidate_from_data(candidate: CandidateProfile, data: dict | list) -> None:
    """将 LLM 解析出的候选人信息写回 CandidateProfile（in-place）。"""
    if not isinstance(data, dict):
        return
    if data.get("name"):
        candidate.name = str(data["name"])
    if data.get("email"):
        candidate.email = str(data["email"])
    if data.get("phone"):
        candidate.phone = str(data["phone"])
    if data.get("resume_summary"):
        candidate.resume_summary = str(data["resume_summary"])
    if isinstance(data.get("skills"), list):
        candidate.skills = [str(s) for s in data["skills"]]
    if isinstance(data.get("education"), list):
        candidate.education = [
            Education(
                school=e.get("school", ""),
                degree=e.get("degree", ""),
                major=e.get("major", ""),
                start_year=e.get("start_year"),
                end_year=e.get("end_year"),
            )
            for e in data["education"]
            if isinstance(e, dict)
        ]
    if isinstance(data.get("work_experience"), list):
        candidate.work_experience = [
            WorkExperience(
                company=w.get("company", ""),
                title=w.get("title", ""),
                duration=w.get("duration", ""),
                description=w.get("description", ""),
            )
            for w in data["work_experience"]
            if isinstance(w, dict)
        ]
    if isinstance(data.get("projects"), list):
        candidate.projects = [
            ProjectExperience(
                name=p.get("name", ""),
                role=p.get("role", ""),
                tech_stack=list(p.get("tech_stack", [])),
                description=p.get("description", ""),
                highlights=list(p.get("highlights", [])),
            )
            for p in data["projects"]
            if isinstance(p, dict)
        ]


def _extract_json(text: str) -> dict | list:
    """从 LLM 输出中尽力提取 JSON。容错处理 ```json 代码块包裹。"""
    text = text.strip()
    if text.startswith("```"):
        # strip leading code fence header line
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # try to find first { or [ and last } or ]
        start_obj = text.find("{")
        start_arr = text.find("[")
        candidates = [c for c in (start_obj, start_arr) if c >= 0]
        if not candidates:
            raise
        start = min(candidates)
        end_obj = text.rfind("}")
        end_arr = text.rfind("]")
        end = max(end_obj, end_arr)
        if end <= start:
            raise
        return json.loads(text[start : end + 1])