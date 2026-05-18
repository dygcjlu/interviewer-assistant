"""UI Agent 工具函数 — 通过 httpx 调用本地 REST 接口实现面试控制意图。

每个函数均可通过 LLM function calling 路由，也可由 UI 按钮直接调用。
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "http://127.0.0.1:8000"


def _set_base_url(url: str) -> None:
    """允许 main.py 在启动时注入实际端口。"""
    global _BASE_URL
    _BASE_URL = url


async def start_interview(candidate_id: str) -> dict:
    """开始面试。

    Args:
        candidate_id: 候选人 ID，从简历上传结果中获取。

    Returns:
        包含 session_id 和 stage 的字典。
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{_BASE_URL}/api/interview/start",
            json={"candidate_id": candidate_id, "trigger_mode": "auto"},
        )
        resp.raise_for_status()
        return resp.json()


async def stop_interview() -> dict:
    """结束面试，切换到评估阶段。

    Returns:
        包含 session_id、stage 和 total_rounds 的字典。
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{_BASE_URL}/api/interview/stop")
        resp.raise_for_status()
        return resp.json()


async def get_eval_report() -> dict:
    """获取当前面试的评价报告。

    Returns:
        包含 report 对象的字典（dimensions、overall_score、recommendation）。
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{_BASE_URL}/api/interview/eval")
        resp.raise_for_status()
        return resp.json()


async def request_suggestion() -> dict:
    """手动触发 AI 追问建议。

    Returns:
        操作结果字典。
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{_BASE_URL}/api/interview/suggest")
        resp.raise_for_status()
        return resp.json()


async def regenerate_questions(candidate_id: str) -> dict:
    """重新获取候选人的面试题目列表。

    Args:
        candidate_id: 候选人 ID。

    Returns:
        包含 candidate_id、profile 和 questions 的字典。
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_BASE_URL}/api/resume/profile",
            params={"candidate_id": candidate_id},
        )
        resp.raise_for_status()
        return resp.json()


def register_tools(tool_registry) -> None:
    """将所有面试控制工具注册到 ToolRegistry。"""
    tool_registry.register(
        description="开始面试，传入候选人 ID 切换到面试阶段",
        parameters_schema={
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string", "description": "候选人 ID"},
            },
            "required": ["candidate_id"],
        },
    )(start_interview)

    tool_registry.register(
        description="结束面试，切换到评估阶段",
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )(stop_interview)

    tool_registry.register(
        description="获取当前面试的 AI 评价报告",
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )(get_eval_report)

    tool_registry.register(
        description="手动触发 AI 追问建议",
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )(request_suggestion)

    tool_registry.register(
        description="重新提炼候选人面试题目列表",
        parameters_schema={
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string", "description": "候选人 ID"},
            },
            "required": ["candidate_id"],
        },
    )(regenerate_questions)
