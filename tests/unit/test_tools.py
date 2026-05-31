"""Unit tests — tools 模块：file_read、file_write、skill_view、_is_allowed。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.tools.file_read import _is_allowed as read_is_allowed, file_read
from src.tools.file_write import _is_allowed as write_is_allowed, file_write
from src.tools.skill_view import skill_view
from src.tools._context import ToolContext


# ── _is_allowed（file_read / file_write 共用相同逻辑）────────────────────────


@pytest.mark.unit
class TestIsAllowed:
    def test_path_in_allowed_dir_returns_true(self, tmp_path):
        allowed = [str(tmp_path) + "/"]
        target = tmp_path / "file.txt"
        assert read_is_allowed(target, allowed) is True

    def test_path_outside_allowed_dir_returns_false(self, tmp_path):
        allowed = [str(tmp_path / "resumes") + "/"]
        target = tmp_path / "other" / "secret.txt"
        assert read_is_allowed(target, allowed) is False

    def test_path_traversal_blocked(self, tmp_path):
        allowed_dir = tmp_path / "allowed"
        allowed_dir.mkdir()
        allowed = [str(allowed_dir) + "/"]
        # 尝试路径穿越
        bad_path = allowed_dir / ".." / ".." / "etc" / "passwd"
        assert read_is_allowed(bad_path, allowed) is False

    def test_empty_allowed_list_returns_false(self, tmp_path):
        target = tmp_path / "file.txt"
        assert read_is_allowed(target, []) is False

    def test_relative_path_within_allowed(self, tmp_path):
        allowed = [str(tmp_path) + "/"]
        relative = Path(str(tmp_path / "sub" / "file.txt"))
        assert read_is_allowed(relative, allowed) is True


# ── file_read ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFileRead:
    @pytest.mark.asyncio
    async def test_reads_allowed_file(self, tmp_path):
        target = tmp_path / "test.txt"
        target.write_text("内容", encoding="utf-8")
        mock_ctx = ToolContext(allowed_read_dirs=[str(tmp_path) + "/"])
        with patch("src.tools.file_read.ctx", mock_ctx):
            result = await file_read(str(target))
        assert result == "内容"

    @pytest.mark.asyncio
    async def test_returns_error_for_disallowed_path(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        target = other / "secret.txt"
        target.write_text("机密")
        mock_ctx = ToolContext(allowed_read_dirs=[str(tmp_path / "allowed") + "/"])
        with patch("src.tools.file_read.ctx", mock_ctx):
            result = await file_read(str(target))
        assert "错误" in result
        assert "不在允许" in result

    @pytest.mark.asyncio
    async def test_returns_error_for_nonexistent_file(self, tmp_path):
        mock_ctx = ToolContext(allowed_read_dirs=[str(tmp_path) + "/"])
        with patch("src.tools.file_read.ctx", mock_ctx):
            result = await file_read(str(tmp_path / "not_exist.txt"))
        assert "错误" in result
        assert "不存在" in result


# ── file_write ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFileWrite:
    @pytest.mark.asyncio
    async def test_writes_to_allowed_path(self, tmp_path):
        target = tmp_path / "output.txt"
        mock_ctx = ToolContext(allowed_write_dirs=[str(tmp_path) + "/"])
        with patch("src.tools.file_write.ctx", mock_ctx):
            result = await file_write(str(target), "写入内容")
        assert "成功" in result
        assert target.read_text(encoding="utf-8") == "写入内容"

    @pytest.mark.asyncio
    async def test_returns_error_for_disallowed_write_path(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        target = other / "file.txt"
        mock_ctx = ToolContext(allowed_write_dirs=[str(tmp_path / "allowed") + "/"])
        with patch("src.tools.file_write.ctx", mock_ctx):
            result = await file_write(str(target), "内容")
        assert "错误" in result
        assert "不在允许" in result

    @pytest.mark.asyncio
    async def test_write_reports_char_count(self, tmp_path):
        target = tmp_path / "count.txt"
        content = "hello"
        mock_ctx = ToolContext(allowed_write_dirs=[str(tmp_path) + "/"])
        with patch("src.tools.file_write.ctx", mock_ctx):
            result = await file_write(str(target), content)
        assert str(len(content)) in result


# ── skill_view ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSkillView:
    @pytest.mark.asyncio
    async def test_returns_error_when_loader_none(self):
        mock_ctx = ToolContext(skill_loader=None)
        with patch("src.tools.skill_view.ctx", mock_ctx):
            result = await skill_view("any_skill")
        assert "未初始化" in result

    @pytest.mark.asyncio
    async def test_returns_skill_content(self):
        mock_loader = MagicMock()
        from src.framework.skill import SkillContent, SkillMeta
        mock_loader.load_skill.return_value = SkillContent(
            meta=SkillMeta(name="test_skill", description="desc", trigger_hint="hint"),
            full_text="完整技巧内容",
        )
        mock_ctx = ToolContext(skill_loader=mock_loader)
        with patch("src.tools.skill_view.ctx", mock_ctx):
            result = await skill_view("test_skill")
        assert result == "完整技巧内容"

    @pytest.mark.asyncio
    async def test_returns_not_found_message(self):
        mock_loader = MagicMock()
        mock_loader.load_skill.side_effect = FileNotFoundError("not found")
        mock_loader.load_index.return_value = []
        mock_ctx = ToolContext(skill_loader=mock_loader)
        with patch("src.tools.skill_view.ctx", mock_ctx):
            result = await skill_view("missing_skill")
        assert "not found" in result.lower() or "missing_skill" in result

    @pytest.mark.asyncio
    async def test_returns_available_skills_on_not_found(self):
        from src.framework.skill import SkillMeta
        mock_loader = MagicMock()
        mock_loader.load_skill.side_effect = FileNotFoundError("x")
        mock_loader.load_index.return_value = [
            SkillMeta(name="available_skill", description="d", trigger_hint="t")
        ]
        mock_ctx = ToolContext(skill_loader=mock_loader)
        with patch("src.tools.skill_view.ctx", mock_ctx):
            result = await skill_view("missing")
        assert "available_skill" in result
