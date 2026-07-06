"""ToolContext — 工具依赖注入容器（模块级单例）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agents.interview_controller import InterviewController
    from ..agents.main_agent import MainAgent
    from ..agents.resume_agent import ResumeAgent
    from ..framework.prompt_builder import PromptBuilder
    from ..framework.skill import SkillLoader
    from ..storage.memory_module import MemoryModule
    from ..storage.user_memory import UserMemoryStore


@dataclass
class ToolContext:
    main_agent: MainAgent | None = None
    resume_agent: ResumeAgent | None = None
    controller: InterviewController | None = None
    memory_module: MemoryModule | None = None
    user_memory_store: UserMemoryStore | None = None
    prompt_builder: PromptBuilder | None = None
    skill_loader: SkillLoader | None = None
    allowed_read_dirs: list[str] = field(
        default_factory=lambda: ["resumes/", "candidates/"]
    )
    allowed_write_dirs: list[str] = field(
        default_factory=lambda: ["resumes/", "candidates/"]
    )


ctx = ToolContext()
