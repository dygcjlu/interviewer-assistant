"""文件原子写入工具。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_atomic(path: Path, content: str) -> None:
    """原子写入：mkstemp + os.replace，防止写入中途崩溃损坏文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
