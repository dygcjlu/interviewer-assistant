"""file_read — 读取白名单目录内的文本文件。"""

from __future__ import annotations

import logging
from pathlib import Path

from ._context import ctx

logger = logging.getLogger(__name__)

DESCRIPTION = "读取指定路径的文本文件内容（仅限白名单目录，默认 resumes/）"
SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "要读取的文件路径（相对或绝对）",
        },
    },
    "required": ["file_path"],
}


async def file_read(file_path: str) -> str:
    path = Path(file_path)
    allowed = ctx.allowed_read_dirs
    if not _is_allowed(path, allowed):
        return f"错误：路径 {file_path!r} 不在允许的读取目录中（{allowed}）"
    try:
        if not path.exists():
            return f"错误：文件不存在 {file_path}"
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.exception("file_read: failed to read %s", file_path)
        return f"错误：读取文件失败 {exc}"


def _is_allowed(path: Path, allowed_dirs: list[str]) -> bool:
    """检查 path 是否位于允许的目录之一内。

    M6-1: 使用 resolve()（strict=False，路径不存在时也不抛错）将路径规范化为绝对路径。
    resolve() 会解析所有 symlink，因此：
      - `resumes/../../../etc/passwd`  → 解析后不在 resumes/ 下，拒绝
      - resumes/ 下放的 symlink 指向 /etc/passwd → 解析后路径是 /etc/passwd，拒绝
    Windows 短文件名、UNC 路径等同样被 resolve() 规范化，不会绕过检查。
    """
    try:
        resolved = path.resolve()
    except Exception:
        return False
    for d in allowed_dirs:
        try:
            resolved.relative_to(Path(d).resolve())
            return True
        except ValueError:
            pass
    return False
