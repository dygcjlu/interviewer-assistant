"""应用日志初始化 — 主日志 + 错误副本 + 控制台，注入关联字段。"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from src.logging.context import (
    agent_var,
    connection_id_var,
    op_var,
    request_id_var,
    session_id_var,
)

_CONFIGURED = False

_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "[session_id=%(session_id)s request_id=%(request_id)s "
    "connection_id=%(connection_id)s agent=%(agent)s op=%(op)s]: "
    "%(message)s"
)

_THIRD_PARTY_LEVELS: dict[str, int] = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "openai": logging.WARNING,
    "openai._base_client": logging.WARNING,
    "urllib3": logging.WARNING,
}


class _ContextFilter(logging.Filter):
    """将 contextvars 注入 LogRecord，供 Formatter 使用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.connection_id = connection_id_var.get()
        record.session_id = session_id_var.get()
        record.agent = agent_var.get()
        record.op = op_var.get()
        return True


def setup_logging(
    log_dir: Path | str | None = None,
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """配置根 logger：控制台 + logs/app.log + logs/app.error.log。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    # Windows 控制台默认 GBK 编码，中文日志重定向到文件时会乱码，统一 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    context_filter = _ContextFilter()
    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.addFilter(context_filter)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_dir is not None:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        app_log = log_path / "app.log"
        app_handler = logging.handlers.RotatingFileHandler(
            app_log,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        app_handler.setLevel(level)
        app_handler.addFilter(context_filter)
        app_handler.setFormatter(formatter)
        root.addHandler(app_handler)

        error_log = log_path / "app.error.log"
        error_handler = logging.handlers.RotatingFileHandler(
            error_log,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.addFilter(context_filter)
        error_handler.setFormatter(formatter)
        root.addHandler(error_handler)

    if log_dir is not None:
        log_path = Path(log_dir)
        llm_log = log_path / "llm.log"
        llm_handler = logging.handlers.RotatingFileHandler(
            llm_log,
            maxBytes=20 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        llm_handler.setLevel(logging.DEBUG)
        llm_handler.addFilter(context_filter)
        llm_handler.setFormatter(formatter)
        llm_logger = logging.getLogger("src.llm.client")
        llm_logger.setLevel(logging.DEBUG)
        llm_logger.addHandler(llm_handler)

    for name, lvl in _THIRD_PARTY_LEVELS.items():
        logging.getLogger(name).setLevel(lvl)

    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    _CONFIGURED = True
    logging.getLogger(__name__).info(
        "Logging configured level=%s log_dir=%s",
        logging.getLevelName(level),
        str(log_dir) if log_dir is not None else "(console only)",
    )
