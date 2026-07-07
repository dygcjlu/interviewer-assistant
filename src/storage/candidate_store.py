"""CandidateStore：候选人 CRUD + 简报 + 结构化问题清单的文件存储。

目录布局（相对 `root`）：
  index.md                          # 全局候选人目录
  {candidate_id}/
  ├── profile.md                    # 候选人档案（YAML frontmatter + 简历全文）
  ├── resume.pdf                    # 原始 PDF
  ├── brief.md                      # 面试简报
  └── questions.json                # 结构化问题清单
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from ..models.candidate import CandidateProfile
from ..utils import write_atomic as _write_atomic
from ._store_common import (
    _build_candidates_index,
    _build_profile_md,
    _parse_frontmatter,
    _profile_from_meta,
)

logger = logging.getLogger(__name__)


class CandidateStore:
    """基于文件系统的候选人档案 / 简报 / 问题清单管理。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._index_path = root / "index.md"

    # ─── 内部路径工具 ─────────────────────────────────────────────────

    def _candidate_dir(self, candidate_id: str) -> Path:
        return self._root / candidate_id

    def _profile_path(self, candidate_id: str) -> Path:
        return self._candidate_dir(candidate_id) / "profile.md"

    def _brief_path(self, candidate_id: str) -> Path:
        return self._candidate_dir(candidate_id) / "brief.md"

    def _questions_path(self, candidate_id: str) -> Path:
        return self._candidate_dir(candidate_id) / "questions.json"

    # ─── 候选人 index 读写 ────────────────────────────────────────────

    def _read_candidates_index(self) -> list[dict]:
        if not self._index_path.exists():
            return []
        try:
            text = self._index_path.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(text)
            return meta.get("candidates") or []
        except Exception:
            logger.exception("Failed to read candidates index")
            return []

    def _write_candidates_index(self, candidates: list[dict]) -> None:
        _write_atomic(self._index_path, _build_candidates_index(candidates))

    def _read_profile_meta(self, candidate_id: str) -> dict | None:
        path = self._profile_path(candidate_id)
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(text)
            return meta
        except Exception:
            return None

    def read_profile_meta(self, candidate_id: str) -> dict | None:
        """公开版 _read_profile_meta，供其他 store 复用。"""
        return self._read_profile_meta(candidate_id)

    def touch_latest_interview(self, candidate_id: str, date_str: str) -> None:
        """更新 candidates/index.md 中某候选人的 latest_interview 字段。"""
        candidates = self._read_candidates_index()
        for c in candidates:
            if c["id"] == candidate_id:
                c["latest_interview"] = date_str
                break
        self._write_candidates_index(candidates)

    # ─── 候选人 CRUD ─────────────────────────────────────────────────

    async def save_candidate(
        self, profile: CandidateProfile, resume_markdown: str
    ) -> str:
        candidate_id = profile.id or f"c-{uuid.uuid4().hex[:12]}"
        profile.id = candidate_id

        if not profile.created_at:
            profile.created_at = datetime.now().isoformat()

        # 写 profile.md
        profile_path = self._profile_path(candidate_id)
        _write_atomic(profile_path, _build_profile_md(profile, resume_markdown))

        # 更新 candidates/index.md
        candidates = self._read_candidates_index()
        entry = {
            "id": candidate_id,
            "name": profile.name,
            "created_at": profile.created_at[:10],
            "latest_interview": None,
        }
        existing_idx = next(
            (i for i, c in enumerate(candidates) if c["id"] == candidate_id), -1
        )
        if existing_idx >= 0:
            entry["latest_interview"] = candidates[existing_idx].get("latest_interview")
            candidates[existing_idx] = entry
        else:
            candidates.append(entry)
        self._write_candidates_index(candidates)

        logger.info(
            "save_candidate done candidate_id=%s name=%r", candidate_id, profile.name
        )
        return candidate_id

    async def get_candidate(self, candidate_id: str) -> CandidateProfile | None:
        path = self._profile_path(candidate_id)
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(text)
            return _profile_from_meta(meta)
        except Exception:
            logger.exception("get_candidate failed for %s", candidate_id)
            return None

    async def get_resume_markdown(self, candidate_id: str) -> str:
        """返回 profile.md 的正文 Markdown（frontmatter 之后的部分）。"""
        path = self._profile_path(candidate_id)
        if not path.exists():
            return ""
        try:
            text = path.read_text(encoding="utf-8")
            _, body = _parse_frontmatter(text)
            return body
        except Exception:
            logger.exception("get_resume_markdown failed for %s", candidate_id)
            return ""

    async def get_candidate_by_name(self, name: str) -> CandidateProfile | None:
        candidates = self._read_candidates_index()
        for c in candidates:
            if c.get("name") == name:
                return await self.get_candidate(c["id"])
        return None

    async def search_candidates(
        self, keyword: str = "", limit: int = 20, offset: int = 0
    ) -> list[CandidateProfile]:
        candidates = self._read_candidates_index()
        if keyword:
            candidates = [
                c
                for c in candidates
                if keyword.lower() in (c.get("name") or "").lower()
            ]
        paged = candidates[offset : offset + limit]
        results = []
        for c in paged:
            profile = await self.get_candidate(c["id"])
            if profile:
                results.append(profile)
        return results

    async def count_candidates(self, keyword: str = "") -> int:
        """返回符合关键词筛选的候选人总数（不受 limit/offset 影响）。"""
        candidates = self._read_candidates_index()
        if keyword:
            candidates = [
                c
                for c in candidates
                if keyword.lower() in (c.get("name") or "").lower()
            ]
        return len(candidates)

    async def delete_candidate(self, candidate_id: str) -> None:
        cand_dir = self._candidate_dir(candidate_id)
        if cand_dir.exists():
            shutil.rmtree(cand_dir)
        candidates = self._read_candidates_index()
        candidates = [c for c in candidates if c["id"] != candidate_id]
        self._write_candidates_index(candidates)
        logger.info("delete_candidate done candidate_id=%s", candidate_id)

    # ─── 面试简报 ─────────────────────────────────────────────────────

    def save_brief(self, candidate_id: str, content: str) -> None:
        """原子写入候选人简报到 candidates/{id}/brief.md。"""
        path = self._brief_path(candidate_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, content)
        logger.info(
            "save_brief done candidate_id=%s chars=%d", candidate_id, len(content)
        )

    def get_brief(self, candidate_id: str) -> str:
        """读取候选人简报，文件不存在时返回空字符串。"""
        path = self._brief_path(candidate_id)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            logger.exception("get_brief failed for %s", candidate_id)
            return ""

    # ─── 结构化问题清单 ────────────────────────────────────────────────

    def save_questions(self, candidate_id: str, questions: list) -> None:
        """原子写入结构化问题清单（list[dict]）。"""
        import json

        path = self._questions_path(candidate_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, json.dumps(questions, ensure_ascii=False, indent=2))
        logger.info(
            "save_questions done candidate_id=%s count=%d", candidate_id, len(questions)
        )

    def get_questions(self, candidate_id: str) -> list:
        """读取问题清单，不存在时返回空列表。"""
        import json

        path = self._questions_path(candidate_id)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("get_questions failed for %s", candidate_id)
            return []

    def update_question_coverage(
        self,
        candidate_id: str,
        question_id: str,
        covered: bool,
        covered_by: str = "manual",
    ) -> bool:
        """更新单个问题的覆盖状态。返回是否找到该问题。"""
        questions = self.get_questions(candidate_id)
        for q in questions:
            if q.get("id") == question_id:
                q["covered"] = covered
                q["covered_by"] = covered_by if covered else ""
                self.save_questions(candidate_id, questions)
                return True
        return False
