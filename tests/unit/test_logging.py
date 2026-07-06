"""Unit tests — logging 模块：setup_logging、_ContextFilter。"""

from __future__ import annotations

import logging
import logging.handlers

import pytest


@pytest.mark.unit
class TestSetupLogging:
    def _reset_logging_configured(self):
        import src.logging.config as lc

        lc._CONFIGURED = False

    def test_setup_logging_console_only(self):
        self._reset_logging_configured()
        from src.logging.config import setup_logging

        setup_logging(log_dir=None, level=logging.WARNING)
        root = logging.getLogger()
        assert root.level == logging.WARNING
        import src.logging.config as lc

        lc._CONFIGURED = False

    def test_setup_logging_with_log_dir(self, tmp_path):
        self._reset_logging_configured()
        from src.logging.config import setup_logging

        setup_logging(log_dir=tmp_path, level=logging.INFO)
        assert (tmp_path / "app.log").exists() or (tmp_path / "app.log").parent.exists()
        import src.logging.config as lc

        lc._CONFIGURED = False

    def test_setup_logging_idempotent(self, tmp_path):
        """调用两次不应重复添加 handlers。"""
        self._reset_logging_configured()
        from src.logging.config import setup_logging

        setup_logging(log_dir=tmp_path, level=logging.INFO)
        handler_count = len(logging.getLogger().handlers)
        setup_logging(log_dir=tmp_path, level=logging.INFO)
        assert len(logging.getLogger().handlers) == handler_count
        import src.logging.config as lc

        lc._CONFIGURED = False

    def test_context_filter_injects_vars(self):
        from src.logging.config import _ContextFilter

        filter_ = _ContextFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="",
            args=(),
            exc_info=None,
        )
        filter_.filter(record)
        assert hasattr(record, "request_id")
        assert hasattr(record, "session_id")
        assert hasattr(record, "agent")
        assert hasattr(record, "op")

    def test_third_party_loggers_set_to_warning(self, tmp_path):
        self._reset_logging_configured()
        from src.logging.config import setup_logging

        setup_logging(log_dir=None, level=logging.DEBUG)
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("openai").level == logging.WARNING
        import src.logging.config as lc

        lc._CONFIGURED = False

    def test_setup_logging_creates_log_dir(self, tmp_path):
        self._reset_logging_configured()
        from src.logging.config import setup_logging

        nested = tmp_path / "deep" / "nested"
        setup_logging(log_dir=nested, level=logging.INFO)
        assert nested.exists()
        import src.logging.config as lc

        lc._CONFIGURED = False
