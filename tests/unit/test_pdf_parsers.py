"""Unit tests — PDF 解析器：MineruParser、QwenVLParser、PyMuPDFParser。"""
from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── MineruParser._extract_md_from_zip ─────────────────────────────────────────


@pytest.mark.unit
class TestMineruExtractMd:
    def _make_zip(self, files: dict[str, str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return buf.getvalue()

    def test_extract_full_md(self):
        from src.tools.pdf_parsers.mineru_parser import MineruParser
        zip_bytes = self._make_zip({"full.md": "# Hello", "other.txt": "ignored"})
        result = MineruParser._extract_md_from_zip(zip_bytes)
        assert result == "# Hello"

    def test_extract_any_md_when_no_full(self):
        from src.tools.pdf_parsers.mineru_parser import MineruParser
        zip_bytes = self._make_zip({"result.md": "# Alt"})
        result = MineruParser._extract_md_from_zip(zip_bytes)
        assert result == "# Alt"

    def test_raises_when_no_md_file(self):
        from src.tools.pdf_parsers.mineru_parser import MineruParser
        zip_bytes = self._make_zip({"data.txt": "no markdown here"})
        with pytest.raises(FileNotFoundError):
            MineruParser._extract_md_from_zip(zip_bytes)


@pytest.mark.unit
class TestMineruExtract:
    @pytest.mark.asyncio
    async def test_extract_uses_agent_api_when_no_token(self, tmp_path):
        from src.tools.pdf_parsers.mineru_parser import MineruParser

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test content")

        mock_settings = MagicMock()
        mock_settings.MINERU_API_TOKEN = ""

        with patch("src.tools.pdf_parsers.mineru_parser.get_settings", return_value=mock_settings):
            parser = MineruParser()
            parser._agent = AsyncMock(return_value="# Parsed content")
            result = await parser.extract(str(pdf_file))
            assert result == "# Parsed content"
            parser._agent.assert_called_once_with(str(pdf_file))

    @pytest.mark.asyncio
    async def test_extract_uses_precise_api_when_token_set(self, tmp_path):
        from src.tools.pdf_parsers.mineru_parser import MineruParser

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test content")

        mock_settings = MagicMock()
        mock_settings.MINERU_API_TOKEN = "test-token"

        with patch("src.tools.pdf_parsers.mineru_parser.get_settings", return_value=mock_settings):
            parser = MineruParser()
            parser._precise = AsyncMock(return_value="# Precise content")
            result = await parser.extract(str(pdf_file))
            assert result == "# Precise content"
            parser._precise.assert_called_once_with(str(pdf_file), mock_settings)

    @pytest.mark.asyncio
    async def test_poll_agent_returns_on_done(self):
        from src.tools.pdf_parsers.mineru_parser import MineruParser

        parser = MineruParser()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"data": {"state": "done", "markdown_url": "http://x.md"}})
        ))

        with patch("asyncio.sleep", AsyncMock()):
            result = await parser._poll_agent(mock_client, "task-123")
        assert result["state"] == "done"

    @pytest.mark.asyncio
    async def test_poll_agent_raises_on_failed(self):
        from src.tools.pdf_parsers.mineru_parser import MineruParser

        parser = MineruParser()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"data": {"state": "failed"}})
        ))

        with patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(RuntimeError, match="failed"):
                await parser._poll_agent(mock_client, "task-fail")

    @pytest.mark.asyncio
    async def test_poll_precise_returns_when_all_done(self):
        from src.tools.pdf_parsers.mineru_parser import MineruParser

        parser = MineruParser()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"data": {
                "files": [{"state": "done", "full_zip_url": "http://zip"}],
                "full_zip_url": "http://zip",
            }})
        ))

        with patch("asyncio.sleep", AsyncMock()):
            result = await parser._poll_precise(mock_client, {}, "batch-001")
        assert result["files"][0]["state"] == "done"

    @pytest.mark.asyncio
    async def test_poll_precise_raises_on_failed(self):
        from src.tools.pdf_parsers.mineru_parser import MineruParser

        parser = MineruParser()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"data": {
                "files": [{"state": "failed"}]
            }})
        ))

        with patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(RuntimeError, match="failed"):
                await parser._poll_precise(mock_client, {}, "batch-fail")


# ── QwenVLParser ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestQwenVLParser:
    @pytest.mark.asyncio
    async def test_extract_calls_openai_client(self, tmp_path):
        from src.tools.pdf_parsers.qwen_vl_parser import QwenVLParser
        import src.tools.pdf_parsers.qwen_vl_parser as qvl_module

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test")

        parser = QwenVLParser()

        mock_settings = MagicMock()
        mock_settings.QWEN_API_KEY = "test-key"
        mock_settings.QWEN_API_BASE_URL = "https://api.example.com/v1"
        mock_settings.QWEN_VL_MODEL = "qwen-vl-test"

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_pixmap = MagicMock()
        mock_pixmap.tobytes = MagicMock(return_value=b"fake_image")
        mock_page.get_pixmap = MagicMock(return_value=mock_pixmap)
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))

        mock_openai_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "# 解析结果"
        mock_openai_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch.object(qvl_module, "get_settings", return_value=mock_settings), \
             patch.object(qvl_module, "pymupdf") as mock_pymupdf, \
             patch("openai.AsyncOpenAI", return_value=mock_openai_client):
            mock_pymupdf.open = MagicMock(return_value=mock_doc)
            result = await parser.extract(str(pdf_file))
        assert isinstance(result, str)


# ── PymupdfParser ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPymupdfParser:
    @pytest.mark.asyncio
    async def test_extract_calls_pymupdf4llm(self, tmp_path):
        from src.tools.pdf_parsers.pymupdf_parser import PymupdfParser
        import src.tools.pdf_parsers.pymupdf_parser as pm_module

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"fake pdf")

        parser = PymupdfParser()
        with patch.object(pm_module, "pymupdf4llm") as mock_lib:
            mock_lib.to_markdown = MagicMock(return_value="# PDF Content")
            result = await parser.extract(str(pdf_file))
        assert result == "# PDF Content"
