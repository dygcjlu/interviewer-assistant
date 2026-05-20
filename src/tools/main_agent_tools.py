"""MainAgent 专用工具 — 由 MainAgent 通过 function calling 调用。

包含：delegate_to_resume_agent、update_user_memory、get_session_info、get_candidate_info
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..agents.main_agent import MainAgent
    from ..agents.interview_controller import InterviewController
    from ..agents.resume_agent import ResumeAgent
    from ..storage.memory_module import MemoryModule

logger = logging.getLogger(__name__)

# Module-level references, set by register_tools()
_main_agent: MainAgent | None = None
_resume_agent: ResumeAgent | None = None
_controller: InterviewController | None = None
_memory_module: MemoryModule | None = None
_user_memory_path: Path = Path("USER.md")


def setup_tools(
    main_agent: "MainAgent",
    resume_agent: "ResumeAgent",
    controller: "InterviewController",
    memory_module: "MemoryModule",
    user_memory_path: str = "USER.md",
) -> None:
    global _main_agent, _resume_agent, _controller, _memory_module, _user_memory_path
    _main_agent = main_agent
    _resume_agent = resume_agent
    _controller = controller
    _memory_module = memory_module
    _user_memory_path = Path(user_memory_path)


async def delegate_to_resume_agent(pdf_path: str, instructions: str = "") -> str:
    """委托 ResumeAgent 解析简历并生成题目。

    Args:
        pdf_path: PDF 简历文件路径。
        instructions: 额外指示（如偏好的技术方向）。

    Returns:
        JSON 字符串，包含解析结果和题目清单。
    """
    if _resume_agent is None or _controller is None:
        return json.dumps({"error": "服务未初始化"}, ensure_ascii=False)

    try:
        result = await _resume_agent.execute(pdf_path, instructions)
        # Update controller session candidate with all parsed fields
        session = await _controller.get_session()
        if session is not None and result.get("profile"):
            from ..agents.resume_agent import _update_candidate_from_data
            _update_candidate_from_data(session.candidate, result["profile"])

        # Update MainAgent context
        if _main_agent is not None and session is not None:
            from ..models.session import InterviewQuestion
            questions = result.get("questions", [])
            if questions:
                session.question_plan = [
                    InterviewQuestion(
                        id=i + 1,
                        dimension=q.get("dimension", "通用"),
                        question=q.get("question", ""),
                        follow_ups=q.get("follow_ups", []),
                        difficulty=q.get("difficulty", "medium"),
                    )
                    for i, q in enumerate(questions)
                    if isinstance(q, dict)
                ]
            _main_agent.set_candidate_context(session.candidate, questions)

            # Persist candidate and interview
            if _memory_module is not None:
                try:
                    await _memory_module.save_candidate(session.candidate)
                    if session.question_plan:
                        await _memory_module.save_interview(session)
                except Exception:
                    logger.exception("delegate_to_resume_agent: persist failed")

        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        logger.exception("delegate_to_resume_agent failed")
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


async def update_user_memory(content: str) -> str:
    """将面试官提供的岗位要求或偏好追加写入 USER.md。

    Args:
        content: 要追加的内容文本。

    Returns:
        操作结果描述。
    """
    try:
        current = ""
        if _user_memory_path.exists():
            current = _user_memory_path.read_text(encoding="utf-8")
        updated = current.rstrip() + "\n\n" + content.strip() + "\n"
        _user_memory_path.write_text(updated, encoding="utf-8")

        if _main_agent is not None:
            _main_agent.reload_user_memory()

        logger.info("update_user_memory: appended %d chars", len(content))
        return json.dumps({"success": True, "message": "已保存到面试官偏好记录"}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("update_user_memory failed")
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


async def get_session_info() -> str:
    """查询 InterviewController 当前会话状态。

    Returns:
        JSON 字符串，包含当前 stage 和会话基本信息。
    """
    if _controller is None:
        return json.dumps({"stage": "idle", "message": "控制器未初始化"}, ensure_ascii=False)
    info = _controller.get_session_info()
    return json.dumps(info, ensure_ascii=False)


async def get_candidate_info() -> str:
    """读取当前候选人完整 profile 和题目清单。

    Returns:
        JSON 字符串，包含候选人信息和题目。
    """
    if _controller is None:
        return json.dumps({"error": "控制器未初始化"}, ensure_ascii=False)

    import dataclasses
    session = await _controller.get_session()
    if session is None:
        return json.dumps({"error": "当前没有活跃会话"}, ensure_ascii=False)

    candidate = session.candidate
    profile_dict = dataclasses.asdict(candidate)
    questions = [
        {
            "id": q.id,
            "dimension": q.dimension,
            "question": q.question,
            "follow_ups": list(q.follow_ups),
            "difficulty": q.difficulty,
        }
        for q in session.question_plan
    ]
    return json.dumps(
        {"profile": profile_dict, "questions": questions},
        ensure_ascii=False,
        default=str,
    )


def register_tools(tool_registry: Any) -> None:
    """将 MainAgent 工具注册到 ToolRegistry。"""
    tool_registry.register(
        description="委托简历解析 Agent 解析 PDF 简历并生成面试题目",
        parameters_schema={
            "type": "object",
            "properties": {
                "pdf_path": {"type": "string", "description": "PDF 简历文件路径"},
                "instructions": {"type": "string", "description": "额外指示，如偏好的技术方向"},
            },
            "required": ["pdf_path"],
        },
    )(delegate_to_resume_agent)

    tool_registry.register(
        description="将面试官提供的岗位要求或偏好保存到记忆文件",
        parameters_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要保存的岗位要求或偏好内容"},
            },
            "required": ["content"],
        },
    )(update_user_memory)

    tool_registry.register(
        description="查询当前面试会话的状态信息",
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )(get_session_info)

    tool_registry.register(
        description="获取当前候选人的完整信息和面试题目清单",
        parameters_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )(get_candidate_info)
