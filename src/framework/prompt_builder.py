"""PromptBuilder — 按七层顺序构建各 Agent 的完整 messages 列表。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .context import ContextManager
from .skill import SkillLoader
from .tool_registry import ToolRegistry
from ..models.message import Message
from ..models.session import InterviewSession
from ..storage.user_memory import UserMemoryStore

if TYPE_CHECKING:
    from ..storage.memory_module import MemoryModule

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    name: str
    system_prompt: str
    skill_names: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    # 若为 True，Layer 7 使用 ContextManager.all_rounds 而非滑动窗口
    full_history: bool = False
    # 若为 False，Layer 7 跳过 assistant=[追问建议] 条目
    include_suggestions: bool = True


class PromptBuilder:
    """唯一对外输出 messages 列表的模块，按固定层次顺序构建各 Agent 的完整 prompt。"""

    def __init__(
        self,
        skill_loader: SkillLoader,
        tool_registry: ToolRegistry,
        memory_module: "MemoryModule",
        context_manager: ContextManager,
        user_memory_store: UserMemoryStore | None = None,
    ) -> None:
        self._skill_loader = skill_loader
        self._tool_registry = tool_registry
        self._memory_module = memory_module
        self._context_manager = context_manager
        self._user_memory_store = user_memory_store
        self._user_memory: str = user_memory_store.render() if user_memory_store else ""

    def reload_user_memory(self) -> None:
        """记忆更新后刷新（store 已是最新，无需重读磁盘）。"""
        self._user_memory = self._user_memory_store.render() if self._user_memory_store else ""
        logger.info("PromptBuilder: reloaded user memory (%d chars)", len(self._user_memory))

    def build(self, session: InterviewSession, agent_config: AgentConfig) -> list[Message]:
        """按七层顺序构建完整 messages 列表。所有 system 层合并为单条消息。"""
        system_parts: list[str] = []

        # Layer 1: Agent identity
        system_parts.append(agent_config.system_prompt)

        # Layer 2: Skill index
        if agent_config.skill_names:
            skill_index_text = self._build_skill_index(agent_config.skill_names)
            if skill_index_text:
                system_parts.append(skill_index_text)

        # Layer 3: Tool guidance
        if agent_config.tool_names:
            tool_text = self._build_tool_guidance(agent_config.tool_names)
            if tool_text:
                system_parts.append(tool_text)

        # Layer 4: Candidate long-term memory
        if session.candidate.history_summary:
            system_parts.append(session.candidate.history_summary)

        # Layer 5: Interview fixed zone (候选人信息 + 题目清单 + 岗位要求)
        fixed_zone = _build_fixed_zone(session, self._user_memory)
        if fixed_zone:
            system_parts.append(fixed_zone)

        # Layers 6 & 7: Dynamic context
        context_data = self._context_manager.get_context()

        # Layer 6: Summary zone
        if context_data.summary:
            system_parts.append(context_data.summary)

        messages: list[Message] = [Message(role="system", content="\n\n".join(system_parts))]

        # Layer 7: Conversation history（面试官+候选人合并为一条 user 消息）
        rounds_to_show = (
            self._context_manager.all_rounds
            if agent_config.full_history
            else context_data.window_rounds
        )
        for round_ in rounds_to_show:
            messages.append(
                Message(
                    role="user",
                    content=f"面试官：{round_.interviewer_text}\n候选人：{round_.candidate_text}",
                )
            )
            if agent_config.include_suggestions and round_.llm_suggestion:
                messages.append(
                    Message(role="assistant", content=f"[追问建议] {round_.llm_suggestion}")
                )

        return messages

    # ── internals ─────────────────────────────────────────────────────────────

    def _build_skill_index(self, skill_names: list[str]) -> str:
        try:
            all_skills = self._skill_loader.load_index()
            filtered = [m for m in all_skills if m.name in skill_names]
            if not filtered:
                return ""
            lines = ["可用面试技巧："] + [
                f"- {m.name}: {m.description} [{m.trigger_hint}]" for m in filtered
            ]
            return "\n".join(lines)
        except Exception:
            logger.exception("PromptBuilder: failed to build skill index")
            return ""

    def _build_tool_guidance(self, tool_names: list[str]) -> str:
        lines = ["可用工具："]
        for name in tool_names:
            entry = self._tool_registry.get_tool(name)
            if entry:
                lines.append(f"- {entry.name}: {entry.description}")
        return "\n".join(lines) if len(lines) > 1 else ""


def _build_fixed_zone(session: InterviewSession, user_memory: str = "") -> str:
    c = session.candidate
    lines = [f"候选人：{c.name}"]
    if c.current_position:
        lines.append(f"当前职位：{c.current_position}")
    if c.years_of_experience is not None:
        lines.append(f"工作年限：{c.years_of_experience} 年")
    if c.age is not None:
        lines.append(f"年龄：{c.age}")
    if c.skills:
        lines.append(f"技能：{', '.join(c.skills)}")
    if c.resume_content:
        lines.append(f"\n## 候选人简历\n\n{c.resume_content.strip()}")
    elif c.id:
        lines.append(f"简历档案：candidates/{c.id}/profile.md（可调用 file_read 工具查看完整内容）")
    if session.interview_brief:
        lines.append(f"\n## 面试简报\n\n{session.interview_brief[:3000]}")
    if user_memory:
        lines.append(f"\n## 面试官岗位要求与偏好\n{user_memory.strip()}")
    return "\n".join(lines)