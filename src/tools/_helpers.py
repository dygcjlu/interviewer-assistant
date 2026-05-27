"""共享辅助函数：被多个 tool / agent 复用，避免重复实现。

当前包含：
- ``normalize_questions``：把 LLM 输出的题目列表规范化为统一 schema，兼容中英文键。
"""
from __future__ import annotations


def normalize_questions(data: dict | list) -> list[dict]:
    """将 LLM 输出（dict 或 list）规范化为题目 dict 列表。

    兼容中英文键：``question``/``题目``/``content``、``dimension``/``维度``、
    ``follow_ups``/``追问``、``difficulty``/``难度``；
    输入若是 ``{"questions": [...]}`` 或 ``{"题目": [...]}`` 也支持。

    返回元素统一为 ``{dimension, question, follow_ups, difficulty}``。
    空 question 会被丢弃。
    """
    if isinstance(data, dict):
        raw = data.get("questions", data.get("题目", []))
        if not isinstance(raw, list):
            return []
        data = raw
    if not isinstance(data, list):
        return []

    normalized: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        question = (
            item.get("question")
            or item.get("题目")
            or item.get("content")
            or ""
        )
        if not str(question).strip():
            continue
        follow_ups = item.get("follow_ups") or item.get("追问") or []
        if isinstance(follow_ups, str):
            follow_ups = [follow_ups]
        normalized.append(
            {
                "dimension": item.get("dimension") or item.get("维度") or "通用",
                "question": str(question),
                "follow_ups": [str(f) for f in follow_ups if f],
                "difficulty": item.get("difficulty") or item.get("难度") or "medium",
            }
        )
    return normalized
