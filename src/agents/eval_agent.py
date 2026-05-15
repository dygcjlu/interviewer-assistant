"""EvalAgent — 评价报告生成。"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime

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
        if request.type == "generate_eval":
            return await self._generate_eval(request)
        return AgentResponse(
            success=False, error=f"Unknown request type: {request.type!r}"
        )

    # ── internals ─────────────────────────────────────────────────────────────

    async def _generate_eval(self, request: AgentRequest) -> AgentResponse:
        session = request.session
        if not session.rounds:
            return AgentResponse(success=False, error="尚无对话记录，无法生成评价")

        conversation = "\n\n".join(
            f"第 {r.round_number} 轮\n面试官: {r.interviewer_text}\n候选人: {r.candidate_text}"
            for r in session.rounds
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
            logger.exception("EvalAgent: LLM call failed")
            return AgentResponse(success=False, error=str(exc))

        try:
            data = _parse_eval_json(result_text)
        except json.JSONDecodeError:
            logger.warning("EvalAgent: LLM output is not valid JSON; using fallback")
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
            logger.exception("EvalAgent: save_eval_report failed")

        # 异步整合长期记忆，不阻塞返回；持有 task 引用避免 GC 提前回收
        try:
            self._consolidate_task = asyncio.get_running_loop().create_task(
                self._memory_module.consolidate_memory(session)
            )
        except RuntimeError:
            logger.warning("EvalAgent: no running event loop, skipping consolidate_memory")

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