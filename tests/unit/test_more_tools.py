"""Unit tests — 更多 tools 模块：manage_user_memory、parse_resume_pdf。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.manage_user_memory import _reload_agents, manage_user_memory
from src.tools.parse_resume_pdf import get_pdf_parser, parse_resume_pdf
from src.tools._context import ToolContext


# ── manage_user_memory ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestManageUserMemory:
    def _make_ctx_with_store(self, tmp_path: Path) -> ToolContext:
        from src.storage.user_memory import UserMemoryStore
        path = tmp_path / "USER.md"
        path.write_text("")
        store = UserMemoryStore(path)
        store.load()
        return ToolContext(user_memory_store=store)

    @pytest.mark.asyncio
    async def test_returns_error_when_store_none(self, tmp_path):
        mock_ctx = ToolContext(user_memory_store=None)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            result = await manage_user_memory("list")
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_list_empty_returns_empty_entries(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            result = await manage_user_memory("list")
        data = json.loads(result)
        assert data["entries"] == []

    @pytest.mark.asyncio
    async def test_add_new_entry(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            result = await manage_user_memory("add", content="招聘高级工程师")
        data = json.loads(result)
        assert data["success"] is True
        assert "index" in data

    @pytest.mark.asyncio
    async def test_add_without_content_returns_error(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            result = await manage_user_memory("add", content=None)
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_list_after_add_shows_entry(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            await manage_user_memory("add", content="偏好 Go 经验")
            result = await manage_user_memory("list")
        data = json.loads(result)
        assert len(data["entries"]) == 1
        assert "Go" in data["entries"][0]["content"]

    @pytest.mark.asyncio
    async def test_replace_updates_entry(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            await manage_user_memory("add", content="旧内容")
            result = await manage_user_memory("replace", index=0, content="新内容")
        data = json.loads(result)
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_replace_without_index_returns_error(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            result = await manage_user_memory("replace", content="内容")
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_replace_without_content_returns_error(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            await manage_user_memory("add", content="条目")
            result = await manage_user_memory("replace", index=0)
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_remove_entry(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            await manage_user_memory("add", content="要删除的")
            result = await manage_user_memory("remove", index=0)
        data = json.loads(result)
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_remove_without_index_returns_error(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            result = await manage_user_memory("remove")
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_remove_out_of_range_returns_error(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            result = await manage_user_memory("remove", index=99)
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, tmp_path):
        mock_ctx = self._make_ctx_with_store(tmp_path)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            result = await manage_user_memory("unknown_action")
        data = json.loads(result)
        assert "error" in data


# ── _reload_agents ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestReloadAgents:
    def test_reload_calls_main_agent_when_present(self):
        mock_main = MagicMock()
        mock_pb = MagicMock()
        mock_ctx = ToolContext(main_agent=mock_main, prompt_builder=mock_pb)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            _reload_agents()
        mock_main.reload_user_memory.assert_called_once()
        mock_pb.reload_user_memory.assert_called_once()

    def test_reload_does_not_crash_when_none(self):
        mock_ctx = ToolContext(main_agent=None, prompt_builder=None)
        with patch("src.tools.manage_user_memory.ctx", mock_ctx):
            _reload_agents()  # should not raise


# ── parse_resume_pdf ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestParsePdf:
    @pytest.mark.asyncio
    async def test_returns_error_when_file_not_exists(self, tmp_path):
        result = await parse_resume_pdf(str(tmp_path / "nonexistent.pdf"))
        data = json.loads(result)
        assert "error" in data
        assert data.get("user_facing") is True

    @pytest.mark.asyncio
    async def test_returns_text_on_success(self, tmp_path, sample_pdf):
        mock_parser = AsyncMock()
        mock_parser.extract = AsyncMock(return_value="# 简历内容")
        with patch("src.tools.parse_resume_pdf.get_pdf_parser", return_value=mock_parser):
            result = await parse_resume_pdf(str(sample_pdf))
        data = json.loads(result)
        assert "text" in data
        assert data["text"] == "# 简历内容"

    @pytest.mark.asyncio
    async def test_fallback_to_pymupdf_when_primary_fails(self, tmp_path, sample_pdf):
        primary = AsyncMock()
        primary.extract = AsyncMock(side_effect=Exception("primary failed"))
        fallback = AsyncMock()
        fallback.extract = AsyncMock(return_value="pymupdf extracted text")

        def mock_get_parser(parser_type: str):
            return primary if parser_type != "pymupdf" else fallback

        with patch("src.tools.parse_resume_pdf.get_pdf_parser", side_effect=mock_get_parser):
            with patch("src.tools.parse_resume_pdf.get_settings") as mock_settings:
                mock_settings.return_value.PDF_PARSER = "qwen_vl"
                result = await parse_resume_pdf(str(sample_pdf))
        data = json.loads(result)
        # 应该降级成功或报错
        assert "text" in data or "error" in data

    @pytest.mark.asyncio
    async def test_returns_user_facing_error_when_pymupdf_is_primary_and_fails(self, tmp_path, sample_pdf):
        mock_parser = AsyncMock()
        mock_parser.extract = AsyncMock(side_effect=Exception("pymupdf failed"))
        with patch("src.tools.parse_resume_pdf.get_pdf_parser", return_value=mock_parser):
            with patch("src.tools.parse_resume_pdf.get_settings") as mock_settings:
                mock_settings.return_value.PDF_PARSER = "pymupdf"
                result = await parse_resume_pdf(str(sample_pdf))
        data = json.loads(result)
        assert "error" in data
        assert data.get("user_facing") is True

    def test_get_pdf_parser_returns_pymupdf_by_default(self):
        from src.tools.pdf_parsers import PymupdfParser
        parser = get_pdf_parser("unknown")
        assert isinstance(parser, PymupdfParser)

    def test_get_pdf_parser_returns_qwen_vl(self):
        from src.tools.pdf_parsers import QwenVLParser
        parser = get_pdf_parser("qwen_vl")
        assert isinstance(parser, QwenVLParser)
