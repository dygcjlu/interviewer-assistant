"""Skill tools — skills_list and skill_view exposed as ToolRegistry-compatible functions."""
from __future__ import annotations

import logging
from typing import Callable, Awaitable

from ..framework.skill import SkillLoader

logger = logging.getLogger(__name__)


def make_skill_tools(skill_loader: SkillLoader) -> tuple[
    Callable[[], Awaitable[str]],
    Callable[[str], Awaitable[str]],
]:
    """Factory: returns (skills_list_fn, skill_view_fn) bound to the given SkillLoader."""

    async def skills_list() -> str:
        """列出当前可用的面试技巧索引（名称 + 一句话描述）。"""
        index = skill_loader.load_index()
        if not index:
            return "暂无可用的面试技巧。"
        lines = [f"- {m.name}: {m.description} | 使用时机: {m.trigger_hint}" for m in index]
        return "\n".join(lines)

    async def skill_view(name: str) -> str:
        """加载指定面试技巧的完整内容。"""
        try:
            content = skill_loader.load_skill(name)
            return content.full_text
        except FileNotFoundError:
            available = [m.name for m in skill_loader.load_index()]
            return f"Skill '{name}' not found. Available: {available}"
        except Exception as exc:
            logger.exception("skill_view: failed to load skill %r", name)
            return f"Error loading skill '{name}': {exc}"

    return skills_list, skill_view