"""请求关联上下文 — 通过 contextvars 在单次请求/连接内传播标识。"""
from __future__ import annotations

import contextvars

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
connection_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("connection_id", default="-")
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="-")
agent_var: contextvars.ContextVar[str] = contextvars.ContextVar("agent", default="-")
op_var: contextvars.ContextVar[str] = contextvars.ContextVar("op", default="-")


def bind_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


def bind_connection_id(connection_id: str) -> None:
    connection_id_var.set(connection_id)


def bind_session_id(session_id: str | None) -> None:
    session_id_var.set(session_id or "-")


def bind_agent(agent: str | None) -> None:
    agent_var.set(agent or "-")


def bind_op(op: str | None) -> None:
    op_var.set(op or "-")


def text_summary(text: str, preview_len: int = 80) -> str:
    """返回文本长度与可选前缀，避免记录全文。"""
    length = len(text)
    if length == 0:
        return "len=0"
    if length <= preview_len:
        return f"len={length}"
    return f"len={length} preview={text[:preview_len]!r}"


def truncate(text: str, max_len: int = 1000) -> str:
    """截断长文本至 max_len 字，超出部分标注 ...(truncated)。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...(truncated)"
