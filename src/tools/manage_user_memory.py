"""manage_user_memory — 面试官偏好记忆的条目化管理工具。

支持四种操作：
- list   : 列出所有条目及其索引，供后续 replace/remove 使用
- add    : 追加新条目
- replace: 替换指定索引的条目（先 list 获取索引）
- remove : 删除指定索引的条目（先 list 获取索引）
"""
from __future__ import annotations

import json
import logging

from ._context import ctx

logger = logging.getLogger(__name__)

DESCRIPTION = "管理面试官偏好记忆：列出/新增/替换/删除条目"

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "add", "replace", "remove"],
            "description": "操作类型：list=列出所有条目, add=追加新条目, replace=替换条目, remove=删除条目",
        },
        "index": {
            "type": "integer",
            "description": "条目索引（replace/remove 时必填，从 list 操作返回结果中获取）",
        },
        "content": {
            "type": "string",
            "description": "条目内容（add/replace 时必填）",
        },
    },
    "required": ["action"],
}


async def manage_user_memory(action: str, index: int | None = None, content: str | None = None) -> str:
    store = ctx.user_memory_store
    if store is None:
        return json.dumps({"error": "user_memory_store 未初始化"}, ensure_ascii=False)

    try:
        if action == "list":
            entries = store.list_entries()
            if not entries:
                return json.dumps(
                    {"entries": [], "message": "记忆列表为空"},
                    ensure_ascii=False,
                )
            return json.dumps({"entries": entries}, ensure_ascii=False)

        if action == "add":
            if not content:
                return json.dumps({"error": "add 操作需要提供 content"}, ensure_ascii=False)
            new_index = store.add(content)
            _reload_agents()
            logger.info("manage_user_memory: added entry[%d] (%d chars)", new_index, len(content))
            return json.dumps(
                {"success": True, "message": f"已添加为条目 {new_index}", "index": new_index},
                ensure_ascii=False,
            )

        if action == "replace":
            if index is None:
                return json.dumps({"error": "replace 操作需要提供 index"}, ensure_ascii=False)
            if not content:
                return json.dumps({"error": "replace 操作需要提供 content"}, ensure_ascii=False)
            store.replace(index, content)
            _reload_agents()
            logger.info("manage_user_memory: replaced entry[%d]", index)
            return json.dumps(
                {"success": True, "message": f"已替换条目 {index}"},
                ensure_ascii=False,
            )

        if action == "remove":
            if index is None:
                return json.dumps({"error": "remove 操作需要提供 index"}, ensure_ascii=False)
            store.remove(index)
            _reload_agents()
            logger.info("manage_user_memory: removed entry[%d]", index)
            return json.dumps(
                {"success": True, "message": f"已删除条目 {index}"},
                ensure_ascii=False,
            )

        return json.dumps({"error": f"未知操作: {action!r}"}, ensure_ascii=False)

    except (IndexError, ValueError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("manage_user_memory failed action=%s", action)
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


def _reload_agents() -> None:
    """通知 MainAgent 和 PromptBuilder 重新加载记忆（无需重读磁盘，store 已是最新）。"""
    if ctx.main_agent is not None:
        ctx.main_agent.reload_user_memory()
    if ctx.prompt_builder is not None:
        ctx.prompt_builder.reload_user_memory()
