from __future__ import annotations

from dataclasses import dataclass, field


def update_candidate_from_data(candidate: "CandidateProfile", data: dict | list) -> None:
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
    if data.get("current_position") is not None:
        candidate.current_position = str(data["current_position"])
    if data.get("years_of_experience") is not None:
        try:
            candidate.years_of_experience = int(data["years_of_experience"])
        except (TypeError, ValueError):
            pass
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


@dataclass
class Education:
    school: str
    degree: str                            # "本科" | "硕士" | "博士" 等
    major: str
    start_year: int | None = None
    end_year: int | None = None


@dataclass
class WorkExperience:
    company: str
    title: str
    duration: str                          # "2022.03 - 2024.06"
    description: str


@dataclass
class ProjectExperience:
    name: str
    role: str
    tech_stack: list[str]
    description: str
    highlights: list[str]


@dataclass
class CandidateProfile:
    id: str
    name: str
    email: str | None = None
    phone: str | None = None
    education: list[Education] = field(default_factory=list)
    work_experience: list[WorkExperience] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    projects: list[ProjectExperience] = field(default_factory=list)
    resume_text: str = ""
    resume_summary: str = ""
    history_summary: str | None = None
    age: int | None = None
    resume_markdown_path: str | None = None
    resume_pdf_path: str | None = None
    years_of_experience: int | None = None
    current_position: str | None = None
