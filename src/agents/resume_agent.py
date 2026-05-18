"""ResumeAgent — 简历解析与面试题目生成。"""
from __future__ import annotations

import dataclasses
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
        logger.info("ResumeAgent parse_resume start file_path=%r", file_path)

        messages = self.prompt_builder.build(request.session, self.config)
        messages.append(
            Message(
                role="user",
                content=(
                    f"请解析候选人简历文件：{file_path}\n"
                    "请调用 parse_resume_pdf 工具读取 PDF 内容，"
                    "然后以 JSON 格式输出包含以下字段的候选人结构化信息：\n"
                    "name, email, phone, age（整数，无则 null）, "
                    "education, work_experience, skills, projects, resume_summary"
                ),
            )
        )

        try:
            result_text = await self._run_with_tools(messages)
            data = _extract_json(result_text)
            _update_candidate_from_data(request.session.candidate, data)
            logger.info(
                "ResumeAgent parse_resume done candidate_name=%r",
                request.session.candidate.name or "",
            )
            return AgentResponse(
                success=True,
                data={"profile_data": data, "file_path": file_path},
            )
        except Exception as exc:
            logger.exception("ResumeAgent: parse_resume failed")
            return AgentResponse(success=False, error=str(exc))

    async def _generate_questions(self, request: AgentRequest) -> AgentResponse:
        logger.info("ResumeAgent generate_questions start")
        candidate = request.session.candidate
        profile_json = json.dumps(
            dataclasses.asdict(candidate), ensure_ascii=False, default=str
        )
        messages = self.prompt_builder.build(request.session, self.config)
        messages.append(
            Message(
                role="user",
                content=(
                    "请根据以下候选人结构化信息生成 8-12 道面试题目清单。\n"
                    "仅输出 JSON 数组，不要调用任何工具。每道题目必须包含字段：\n"
                    '- dimension（如"项目经验"、"系统设计"）\n'
                    '- question（题目正文）\n'
                    '- follow_ups（2-3 个追问，字符串数组）\n'
                    '- difficulty（easy / medium / hard）\n\n'
                    f"候选人信息：\n{profile_json}"
                ),
            )
        )

        try:
            response = await self.llm_client.chat(messages, tools=None)
            result_text = response.content or ""
            questions_data = _normalize_questions(_extract_json(result_text))
            if not questions_data:
                return AgentResponse(
                    success=False,
                    error="题目生成结果为空，请检查 LLM 返回格式",
                )
            logger.info(
                "ResumeAgent generate_questions done questions_count=%d",
                len(questions_data),
            )
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
    if data.get("age") is not None:
        try:
            candidate.age = int(data["age"])
        except (TypeError, ValueError):
            pass
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