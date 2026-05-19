from __future__ import annotations

from dataclasses import dataclass, field


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
