"""ToolRegistry — 工具注册中心与调度器。"""
from __future__ import annotations

import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..llm.protocol import ToolFunction, ToolSchema
from ..logging import truncate

logger = logging.getLogger(__name__)


@dataclass
class ToolEntry:
    name: str
    description: str
    parameters_schema: dict
    fn: Callable[..., Awaitable[Any]]
    pre_hook: Callable | None = None
    post_hook: Callable | None = None


class ToolRegistry:
    """工具注册中心与调度器。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register(
        self,
        description: str,
        parameters_schema: dict | None = None,
    ) -> Callable:
        """装饰器 — 注册工具函数，自动从函数签名生成 JSON Schema（若未提供）。"""

        def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            schema = parameters_schema or _build_schema(fn)
            entry = ToolEntry(
                name=fn.__name__,
                description=description,
                parameters_schema=schema,
                fn=fn,
            )
            self._tools[fn.__name__] = entry
            logger.debug("ToolRegistry: registered tool %r", fn.__name__)
            return fn

        return decorator

    def get_tool(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def get_schemas(self, names: list[str] | None = None) -> list[ToolSchema]:
        """获取工具 JSON Schema 列表（传入 LLM 的 tools 参数）。"""
        tools = self._tools.values() if names is None else (
            self._tools[n] for n in names if n in self._tools
        )
        return [
            ToolSchema(
                function=ToolFunction(
                    name=t.name,
                    description=t.description,
                    parameters=t.parameters_schema,
                )
            )
            for t in tools
        ]

    async def dispatch(self, name: str, arguments: str) -> str:
        """调度执行工具: schema 校验 → pre_hook → fn(**args) → post_hook → 序列化。"""
        entry = self._tools.get(name)
        if entry is None:
            return json.dumps({"error": f"Unknown tool: {name!r}"})

        try:
            parsed_args: dict = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"Invalid JSON arguments: {exc}"})

        logger.info("tool_call: %s args=%s", name, truncate(arguments or "{}"))
        start = time.perf_counter()
        try:
            if entry.pre_hook:
                entry.pre_hook(**parsed_args)
            result = await entry.fn(**parsed_args)
            if entry.post_hook:
                entry.post_hook(result)
        except Exception:
            logger.exception("ToolRegistry: error dispatching tool %r", name)
            return json.dumps({"error": f"Tool {name!r} raised an exception"})

        elapsed_ms = (time.perf_counter() - start) * 1000
        if isinstance(result, str):
            serialized = result
        else:
            serialized = json.dumps(result, ensure_ascii=False, default=str)
        logger.info(
            "tool_result: %s result=%s elapsed_ms=%.1f",
            name,
            truncate(serialized),
            elapsed_ms,
        )
        return serialized


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_schema(fn: Callable) -> dict:
    """Auto-generate a minimal JSON Schema from a function's signature."""
    sig = inspect.signature(fn)
    properties: dict = {}
    required: list[str] = []
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        properties[param_name] = {"type": "string"}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }