"""skill_view — 加载指定面试技巧完整内容。"""
from __future__ import annotations

import logging

from ._context import ctx

logger = logging.getLogger(__name__)

DESCRIPTION = "查看指定面试技巧的完整内容"
SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "技巧名称"},
    },
    "required": ["name"],
}


async def skill_view(name: str) -> str:
    if ctx.skill_loader is None:
        return "技巧加载器未初始化。"
    try:
        content = ctx.skill_loader.load_skill(name)
        return content.full_text
    except FileNotFoundError:
        available = [m.name for m in ctx.skill_loader.load_index()]
        return f"Skill '{name}' not found. Available: {available}"
    except Exception as exc:
        logger.exception("skill_view: failed to load skill %r", name)
        return f"Error loading skill '{name}': {exc}"
