"""ToolContext — 工具依赖注入容器（模块级单例）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agents.interview_controller import InterviewController
    from ..agents.main_agent import MainAgent
    from ..agents.resume_agent import ResumeAgent
    from ..framework.prompt_builder import PromptBuilder
    from ..framework.skill import SkillLoader
    from ..models.candidate import CandidateProfile
    from ..storage.memory_module import MemoryModule
    from ..storage.user_memory import UserMemoryStore


@dataclass
class PendingResumeDuplicate:
    """一条待决议的候选人重名记录（解析出真实姓名后与已持久化候选人撞名）。

    生成于 `dispatch_to_agent._apply_side_effects` 的 parse_done 判重命中分支，
    由 `POST /api/resume/resolve-duplicate` 消费后从 `ToolContext.pending_duplicates`
    中移除；不做持久化、不做 TTL 过期（详见 task-4.1-report.md 2.3 节）。
    """

    pending_id: str
    session_id: str
    new_profile: CandidateProfile
    resume_markdown: str
    existing_candidate_id: str
    existing_candidate_name: str
    created_at: float = field(default_factory=time.time)


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
    pending_duplicates: dict[str, PendingResumeDuplicate] = field(default_factory=dict)


ctx = ToolContext()
