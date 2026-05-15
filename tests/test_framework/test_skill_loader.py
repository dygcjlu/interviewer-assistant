"""Tests for SkillLoader."""
import pytest
from pathlib import Path

from src.framework.skill import SkillLoader, SkillMeta, SkillContent


SKILLS_DIR = Path(__file__).parents[2] / "skills"


def test_load_index_returns_skill_meta_list() -> None:
    loader = SkillLoader(SKILLS_DIR)
    index = loader.load_index()
    assert isinstance(index, list)
    assert len(index) >= 4
    names = {m.name for m in index}
    assert "deep_dive" in names
    assert "dimension_switch" in names
    assert "behavioral_probe" in names
    assert "resume_anchor" in names


def test_skill_meta_fields_populated() -> None:
    loader = SkillLoader(SKILLS_DIR)
    index = loader.load_index()
    for meta in index:
        assert isinstance(meta, SkillMeta)
        assert meta.name
        assert meta.description
        assert meta.trigger_hint


def test_load_skill_returns_content() -> None:
    loader = SkillLoader(SKILLS_DIR)
    content = loader.load_skill("deep_dive")
    assert isinstance(content, SkillContent)
    assert content.meta.name == "deep_dive"
    assert len(content.full_text) > 50


def test_load_skill_full_text_contains_frontmatter() -> None:
    loader = SkillLoader(SKILLS_DIR)
    content = loader.load_skill("deep_dive")
    assert "---" in content.full_text


def test_load_skill_missing_raises() -> None:
    loader = SkillLoader(SKILLS_DIR)
    with pytest.raises(FileNotFoundError):
        loader.load_skill("nonexistent_skill_xyz")


def test_load_index_empty_dir(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    index = loader.load_index()
    assert index == []


def test_load_index_nonexistent_dir(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path / "does_not_exist")
    index = loader.load_index()
    assert index == []