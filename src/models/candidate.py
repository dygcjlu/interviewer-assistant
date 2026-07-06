from __future__ import annotations

from dataclasses import dataclass, field


def update_candidate_from_data(candidate: CandidateProfile, data: dict | list) -> None:
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
    if data.get("current_position") is not None:
        candidate.current_position = str(data["current_position"])
    if data.get("years_of_experience") is not None:
        try:
            candidate.years_of_experience = int(data["years_of_experience"])
        except (TypeError, ValueError):
            pass
    if isinstance(data.get("skills"), list):
        candidate.skills = [str(s) for s in data["skills"]]


@dataclass
class CandidateProfile:
    id: str
    name: str
    email: str | None = None
    phone: str | None = None
    age: int | None = None
    skills: list[str] = field(default_factory=list)
    years_of_experience: int | None = None
    current_position: str | None = None
    created_at: str = ""
    resume_pdf: str = ""
    # 运行时瞬态字段（不持久化到 profile.md frontmatter）
    history_summary: str | None = None
    resume_content: str = (
        ""  # profile.md 正文（Markdown），由 create_session 从文件加载
    )
