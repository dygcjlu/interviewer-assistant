"""SkillLoader — 从文件系统加载 Skill 元数据和完整内容。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SkillMeta:
    name: str
    description: str
    trigger_hint: str


@dataclass
class SkillContent:
    meta: SkillMeta
    full_text: str


class SkillLoader:
    """从 skills/{name}/SKILL.md 文件系统动态加载 Skill。"""

    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = Path(skills_dir)

    def load_index(self) -> list[SkillMeta]:
        """扫描 skills/ 目录，读取每个 SKILL.md 的 frontmatter，返回索引列表。"""
        result: list[SkillMeta] = []
        if not self._skills_dir.exists():
            logger.warning(
                "SkillLoader: skills_dir does not exist: %s", self._skills_dir
            )
            return result
        for skill_dir in sorted(self._skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                meta = self._parse_frontmatter(skill_file.read_text(encoding="utf-8"))
                result.append(meta)
            except Exception:
                logger.exception("SkillLoader: failed to parse %s", skill_file)
        return result

    def load_skill(self, name: str) -> SkillContent:
        """按需加载指定 Skill 的完整 SKILL.md 内容。"""
        skill_file = self._skills_dir / name / "SKILL.md"
        if not skill_file.exists():
            raise FileNotFoundError(
                f"Skill not found: {name!r} (looked in {skill_file})"
            )
        full_text = skill_file.read_text(encoding="utf-8")
        meta = self._parse_frontmatter(full_text)
        return SkillContent(meta=meta, full_text=full_text)

    # ── internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_frontmatter(text: str) -> SkillMeta:
        """Parse YAML frontmatter delimited by --- lines."""
        lines = text.split("\n")
        if lines[0].strip() != "---":
            raise ValueError("SKILL.md does not start with '---' frontmatter")
        end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
        if end is None:
            raise ValueError("SKILL.md frontmatter not closed with '---'")
        frontmatter_text = "\n".join(lines[1:end])
        data = yaml.safe_load(frontmatter_text) or {}
        return SkillMeta(
            name=data.get("name", ""),
            description=data.get("description", ""),
            trigger_hint=data.get("trigger_hint", ""),
        )
