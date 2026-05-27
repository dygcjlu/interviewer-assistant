"""数值容错工具。"""
from __future__ import annotations


def safe_float(value, default: float = 0.0) -> float:
    """容错的 float 转换：脏数据（'N/A'、None、非数值字符串等）退回 default。"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default
