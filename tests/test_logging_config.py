"""日志配置与上下文关联字段测试。"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

import src.logging_config as logging_config_module
from src.log_context import (
    bind_agent,
    bind_connection_id,
    bind_op,
    bind_request_id,
    bind_session_id,
    text_summary,
)
from src.logging_config import setup_logging


@pytest.fixture(autouse=True)
def reset_logging_configured() -> None:
    """每个测试前重置配置标志，避免 handler 累积。"""
    logging_config_module._CONFIGURED = False
    root = logging.getLogger()
    root.handlers.clear()
    yield
    logging_config_module._CONFIGURED = False
    root.handlers.clear()


def test_setup_logging_creates_app_and_error_files(tmp_path: Path) -> None:
    setup_logging(log_dir=tmp_path, level=logging.INFO)
    logger = logging.getLogger("test.app")
    logger.info("info line")
    logger.error("error line")

    assert (tmp_path / "app.log").exists()
    assert (tmp_path / "app.error.log").exists()
    app_content = (tmp_path / "app.log").read_text(encoding="utf-8")
    error_content = (tmp_path / "app.error.log").read_text(encoding="utf-8")
    assert "info line" in app_content
    assert "error line" in app_content
    assert "error line" in error_content
    assert "info line" not in error_content


def test_context_fields_in_log_record(tmp_path: Path) -> None:
    setup_logging(log_dir=tmp_path, level=logging.INFO)
    bind_request_id("req-abc")
    bind_session_id("sess-xyz")
    bind_connection_id("conn-1")
    bind_agent("resume")
    bind_op("upload_resume")

    logging.getLogger("test.ctx").info("context test")

    line = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "request_id=req-abc" in line
    assert "session_id=sess-xyz" in line
    assert "connection_id=conn-1" in line
    assert "agent=resume" in line
    assert "op=upload_resume" in line


def test_text_summary_truncates_long_text() -> None:
    long_text = "a" * 100
    summary = text_summary(long_text, preview_len=20)
    assert "len=100" in summary
    assert "preview=" in summary
    assert len(summary) < len(long_text)
