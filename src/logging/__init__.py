"""结构化日志：上下文传播与初始化配置。"""
from src.logging.config import setup_logging
from src.logging.context import (
    agent_var,
    bind_agent,
    bind_connection_id,
    bind_op,
    bind_request_id,
    bind_session_id,
    connection_id_var,
    op_var,
    request_id_var,
    session_id_var,
    text_summary,
    truncate,
)

__all__ = [
    "agent_var",
    "bind_agent",
    "bind_connection_id",
    "bind_op",
    "bind_request_id",
    "bind_session_id",
    "connection_id_var",
    "op_var",
    "request_id_var",
    "session_id_var",
    "setup_logging",
    "text_summary",
    "truncate",
]
