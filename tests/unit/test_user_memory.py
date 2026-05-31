"""Unit tests — UserMemoryStore CRUD 与持久化行为。"""
from __future__ import annotations

import pytest

from src.storage.user_memory import UserMemoryStore


# ── 辅助 ──────────────────────────────────────────────────────────────────────


def _store(tmp_path, char_limit=3000) -> UserMemoryStore:
    path = tmp_path / "USER.md"
    path.write_text("")
    store = UserMemoryStore(path, char_limit=char_limit)
    store.load()
    return store


# ── load ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_load_empty_file_returns_no_entries(tmp_path):
    store = _store(tmp_path)
    assert store.list_entries() == []


@pytest.mark.unit
def test_load_existing_content_parses_entries(tmp_path):
    path = tmp_path / "USER.md"
    path.write_text("招 Go 方向工程师\n\n---\n\n偏好系统设计考察", encoding="utf-8")
    store = UserMemoryStore(path)
    store.load()
    entries = store.list_entries()
    assert len(entries) == 2
    assert entries[0]["index"] == 0
    assert "Go" in entries[0]["content"]


# ── render ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_render_returns_empty_string_when_no_entries(tmp_path):
    store = _store(tmp_path)
    assert store.render() == ""


@pytest.mark.unit
def test_render_concatenates_all_entries(tmp_path):
    store = _store(tmp_path)
    store.add("条目一")
    store.add("条目二")
    rendered = store.render()
    assert "条目一" in rendered
    assert "条目二" in rendered


# ── add ───────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_add_new_entry_appears_in_list(tmp_path):
    store = _store(tmp_path)
    store.add("招聘高级工程师，3 年以上经验")
    entries = store.list_entries()
    assert len(entries) == 1
    assert "招聘高级工程师" in entries[0]["content"]


@pytest.mark.unit
def test_add_multiple_entries_all_stored(tmp_path):
    store = _store(tmp_path)
    store.add("条目 A")
    store.add("条目 B")
    store.add("条目 C")
    assert len(store.list_entries()) == 3


@pytest.mark.unit
def test_add_exceeding_char_limit_raises(tmp_path):
    store = _store(tmp_path, char_limit=20)
    with pytest.raises(ValueError):
        store.add("这是一段超过字符上限的很长很长的内容，超过了设置的上限")


# ── replace ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_replace_updates_content_at_index(tmp_path):
    store = _store(tmp_path)
    store.add("旧内容")
    store.replace(0, "新内容")
    assert store.list_entries()[0]["content"] == "新内容"


@pytest.mark.unit
def test_replace_out_of_range_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises((IndexError, ValueError)):
        store.replace(99, "不存在的索引")


# ── remove ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_remove_deletes_entry_by_index(tmp_path):
    store = _store(tmp_path)
    store.add("将被删除")
    store.add("保留")
    store.remove(0)
    entries = store.list_entries()
    assert len(entries) == 1
    assert "保留" in entries[0]["content"]


@pytest.mark.unit
def test_remove_out_of_range_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises((IndexError, ValueError)):
        store.remove(0)


# ── 持久化 ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_add_persists_to_disk(tmp_path):
    """add 后重新 load，内容仍在。"""
    store = _store(tmp_path)
    store.add("持久化测试")

    store2 = UserMemoryStore(tmp_path / "USER.md")
    store2.load()
    assert len(store2.list_entries()) == 1
    assert "持久化测试" in store2.list_entries()[0]["content"]


@pytest.mark.unit
def test_remove_persists_to_disk(tmp_path):
    """remove 后重新 load，条目消失。"""
    store = _store(tmp_path)
    store.add("会消失的条目")
    store.remove(0)

    store2 = UserMemoryStore(tmp_path / "USER.md")
    store2.load()
    assert store2.list_entries() == []
