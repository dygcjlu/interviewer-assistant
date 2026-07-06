"""file_write — 将文本内容写入白名单目录内的文件。"""

from __future__ import annotations

import logging
from pathlib import Path

from ..utils.atomic_io import write_atomic
from ._context import ctx

logger = logging.getLogger(__name__)

DESCRIPTION = "将文本内容写入指定路径的文件（仅限白名单目录，默认 resumes/）"
SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "要写入的文件路径（相对或绝对）",
        },
        "content": {"type": "string", "description": "要写入的文本内容"},
    },
    "required": ["file_path", "content"],
}


async def file_write(file_path: str, content: str) -> str:
    path = Path(file_path)
    allowed = ctx.allowed_write_dirs
    if not _is_allowed(path, allowed):
        return f"错误：路径 {file_path!r} 不在允许的写入目录中（{allowed}）"
    try:
        # M6-2: 复用公共 write_atomic（mkstemp + os.replace），避免写一半崩溃留下半文件
        write_atomic(path, content)
        logger.info("file_write: wrote %d chars to %s", len(content), file_path)
        return f"已成功写入 {file_path}（{len(content)} 字符）"
    except Exception as exc:
        logger.exception("file_write: failed to write %s", file_path)
        return f"错误：写入文件失败 {exc}"


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
