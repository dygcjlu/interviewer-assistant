"""EvalAgent — 评价报告生成。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime

from src.log_context import bind_op, text_summary

from .base import AgentRequest, AgentResponse, BaseAgent
from ..framework.prompt_builder import AgentConfig, PromptBuilder
from ..framework.tool_registry import ToolRegistry
from ..models.evaluation import DimensionScore, EvalReport
from ..models.message import Message
from ..models.session import InterviewSession
from ..storage.memory_module import MemoryModule
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm.protocol import LLMClient

logger = logging.getLogger(__name__)


class EvalAgent(BaseAgent):
    """评价 Agent — 基于完整对话记录生成 EvalReport。"""

    def __init__(
        self,
        config: AgentConfig,
        prompt_builder: PromptBuilder,
        llm_client: "LLMClient",
        tool_registry: ToolRegistry,
        memory_module: MemoryModule,
    ) -> None:
        super().__init__(config, prompt_builder, llm_client, tool_registry)
        self._memory_module = memory_module
        self._consolidate_task: asyncio.Task | None = None

    async def on_activate(self, session: InterviewSession) -> None:
        logger.info(
            "EvalAgent activated for session %s with %d rounds",
            session.id,
            len(session.rounds),
        )

    async def on_deactivate(self, session: InterviewSession) -> None:
        logger.info("EvalAgent deactivated for session %s", session.id)

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        bind_op(request.type)
        logger.info(
            "EvalAgent handle_request start type=%s session_id=%s",
            request.type,
            request.session.id,
        )
        if request.type == "generate_eval":
            return await self._generate_eval(request)
        logger.error("EvalAgent handle_request unknown type=%r", request.type)
        return AgentResponse(
            success=False, error=f"Unknown request type: {request.type!r}"
        )

    # ── internals ─────────────────────────────────────────────────────────────

    async def _generate_eval(self, request: AgentRequest) -> AgentResponse:
        session = request.session
        start = time.perf_counter()
        if not session.rounds:
            logger.error(
                "EvalAgent generate_eval failed session_id=%s error=no_rounds",
                session.id,
            )
            return AgentResponse(success=False, error="尚无对话记录，无法生成评价")

        logger.info(
            "EvalAgent generate_eval start session_id=%s rounds_count=%d",
            session.id,
            len(session.rounds),
        )

        conversation = "\n\n".join(
            f"第 {r.round_number} 轮\n面试官: {r.interviewer_text}\n候选人: {r.candidate_text}"
            for r in session.rounds
        )

        logger.info(
            "EvalAgent generate_eval conversation %s",
            text_summary(conversation, preview_len=80),
        )

        messages = self.prompt_builder.build(session, self.config)
        messages.append(
            Message(
                role="user",
                content=(
                    f"请根据以下完整面试对话记录生成评价报告：\n\n{conversation}\n\n"
                    "输出 JSON 对象，包含以下字段：\n"
                    "- dimensions: 维度数组，每个含 dimension/score(1-10)/comment/evidence(候选人原话数组)\n"
                    "- overall_score: 综合分(1-10)\n"
                    "- strengths: 优势列表\n"
                    "- weaknesses: 不足列表\n"
                    "- recommendation: strong_hire | hire | weak_hire | no_hire\n"
                    "- summary: 整体评价文字"
                ),
            )
        )

        try:
            result_text = await self._run_with_tools(messages)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "EvalAgent generate_eval LLM failed session_id=%s elapsed_ms=%.1f",
                session.id,
                elapsed_ms,
            )
            return AgentResponse(success=False, error=str(exc))

        try:
            data = _parse_eval_json(result_text)
        except json.JSONDecodeError:
            logger.warning(
                "EvalAgent generate_eval invalid_json session_id=%s response %s",
                session.id,
                text_summary(result_text, preview_len=80),
            )
            data = {}

        report = EvalReport(
            id=str(uuid.uuid4()),
            interview_id=session.id,
            dimensions=[
                DimensionScore(
                    dimension=str(d.get("dimension", "综合")),
                    score=float(d.get("score", 5.0)),
                    comment=str(d.get("comment", "")),
                    evidence=list(d.get("evidence", [])),
                )
                for d in data.get("dimensions", [])
            ],
            overall_score=float(data.get("overall_score", 5.0)),
            strengths=list(data.get("strengths", [])),
            weaknesses=list(data.get("weaknesses", [])),
            recommendation=str(data.get("recommendation", "hire")),
            summary=str(data.get("summary", result_text[:500])),
            generated_at=datetime.now(),
        )

        try:
            await self._memory_module.save_eval_report(report)
        except Exception:
            logger.exception(
                "EvalAgent generate_eval save_eval_report failed report_id=%s",
                report.id,
            )

        # 异步整合长期记忆，不阻塞返回；持有 task 引用避免 GC 提前回收
        try:
            self._consolidate_task = asyncio.get_running_loop().create_task(
                self._memory_module.consolidate_memory(session)
            )
            logger.info(
                "EvalAgent generate_eval consolidate_memory scheduled session_id=%s",
                session.id,
            )
        except RuntimeError:
            logger.warning(
                "EvalAgent generate_eval consolidate_memory skipped session_id=%s",
                session.id,
            )

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "EvalAgent generate_eval done session_id=%s report_id=%s "
            "overall_score=%.1f recommendation=%s dimensions_count=%d elapsed_ms=%.1f",
            session.id,
            report.id,
            report.overall_score,
            report.recommendation,
            len(report.dimensions),
            elapsed_ms,
        )
        return AgentResponse(success=True, data={"report": report})


def _parse_eval_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        result = json.loads(text[start : end + 1])
    if not isinstance(result, dict):
        raise json.JSONDecodeError("Expected object", text, 0)
    return result