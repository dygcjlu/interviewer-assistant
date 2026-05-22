"""update_user_memory — 追加写入 USER.md 面试官偏好。"""
from __future__ import annotations

import json
import logging

from ._context import ctx

logger = logging.getLogger(__name__)

DESCRIPTION = "将面试官提供的岗位要求或偏好保存到记忆文件"
SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string", "description": "要保存的岗位要求或偏好内容"},
    },
    "required": ["content"],
}


async def update_user_memory(content: str) -> str:
    try:
        current = ""
        if ctx.user_memory_path.exists():
            current = ctx.user_memory_path.read_text(encoding="utf-8")
        updated = current.rstrip() + "\n\n" + content.strip() + "\n"
        ctx.user_memory_path.write_text(updated, encoding="utf-8")

        if ctx.main_agent is not None:
            ctx.main_agent.reload_user_memory()
        if ctx.prompt_builder is not None:
            ctx.prompt_builder.reload_user_memory()

        logger.info("update_user_memory: appended %d chars", len(content))
        return json.dumps({"success": True, "message": "已保存到面试官偏好记录"}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("update_user_memory failed")
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
