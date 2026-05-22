"""register_all — 将所有 LLM 工具注册到 ToolRegistry。"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..framework.tool_registry import ToolRegistry

# 所有注册为 LLM 工具的模块名（与文件名、函数名保持一致）
_LLM_TOOLS = [
    "parse_resume_pdf",
    "file_read",
    "file_write",
    "dispatch_to_agent",
    "update_user_memory",
    "skill_view",
]


def register_all(registry: "ToolRegistry") -> None:
    """将 _LLM_TOOLS 中的所有工具注册到 registry。"""
    for name in _LLM_TOOLS:
        mod = importlib.import_module(f".{name}", package=__package__)
        fn = getattr(mod, name)
        schema = getattr(mod, "SCHEMA", None)
        registry.register(description=mod.DESCRIPTION, parameters_schema=schema)(fn)
