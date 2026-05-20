"""EvalAgent — 评价报告生成。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.logging import bind_op, text_summary

from .base import AgentRequest, AgentResponse, BaseAgent
from ..framework.prompt_builder import AgentConfig, PromptBuilder
from ..framework.tool_registry import ToolRegistry
from ..models.evaluation import DimensionScore, EvalReport
from ..models.message import Message
from ..models.session import ConversationRound, InterviewSession
from ..storage.memory_module import MemoryModule

if TYPE_CHECKING:
    from ..llm.protocol import LLMClient

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 30          # 分块模式每块轮次数
_TOKEN_THRESHOLD = 30000  # 超过此 token 估算切换为分块 map-reduce

_EVAL_OUTPUT_INSTRUCTIONS = (
    "输出 JSON 对象，包含以下字段：\n"
    "- dimensions: 维度数组，每个含 dimension/score(1-10)/comment/evidence(候选人原话数组)\n"
    "- overall_score: 综合分(1-10)\n"
    "- strengths: 优势列表\n"
    "- weaknesses: 不足列表\n"
    "- recommendation: strong_hire | hire | weak_hire | no_hire\n"
    "- summary: 整体评价（不少于 200 字，须涵盖技术能力判断、沟通表达风格、岗位匹配度评估）"
)


class EvalAgent(BaseAgent):
    """评价 Agent — 基于完整对话记录生成 EvalReport。"""

    def __init__(
        self,
        config: AgentConfig,
        prompt_builder: PromptBuilder,
        llm_client: "LLMClient",
        tool_registry: ToolRegistry,
        memory_module: MemoryModule,
        user_memory_path: str = "USER.md",
    ) -> None:
        super().__init__(config, prompt_builder, llm_client, tool_registry)
        self._memory_module = memory_module
        self._user_memory_path = Path(user_memory_path)
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

        user_memory = self._read_user_memory()
        base_messages = self._build_base_messages(session, user_memory)

        full_text = _format_rounds(session.rounds)
        logger.info(
            "EvalAgent generate_eval conversation %s",
            text_summary(full_text, preview_len=80),
        )

        estimated_tokens = int(len(full_text) / 1.5)
        if estimated_tokens <= _TOKEN_THRESHOLD:
            logger.info(
                "EvalAgent generate_eval using single-call path estimated_tokens=%d",
                estimated_tokens,
            )
            try:
                result_text = await self._eval_single(base_messages, session.rounds)
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.exception(
                    "EvalAgent generate_eval LLM failed session_id=%s elapsed_ms=%.1f",
                    session.id,
                    elapsed_ms,
                )
                return AgentResponse(success=False, error=str(exc))
        else:
            logger.info(
                "EvalAgent generate_eval using chunked map-reduce path estimated_tokens=%d",
                estimated_tokens,
            )
            try:
                result_text = await self._eval_chunked(base_messages, session.rounds)
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.exception(
                    "EvalAgent generate_eval chunked LLM failed session_id=%s elapsed_ms=%.1f",
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

    def _read_user_memory(self) -> str:
        """读取 USER.md 岗位要求文件，失败时返回空字符串。"""
        try:
            if self._user_memory_path.exists():
                return self._user_memory_path.read_text(encoding="utf-8")
        except Exception:
            logger.warning(
                "EvalAgent: failed to read user memory file %s", self._user_memory_path
            )
        return ""

    def _build_base_messages(self, session: InterviewSession, user_memory: str) -> list[Message]:
        """组装评价用基础系统消息（角色定义 + 岗位要求 + 候选人信息 + 历史记忆）。"""
        messages: list[Message] = [
            Message(role="system", content=self.config.system_prompt)
        ]

        if user_memory.strip():
            messages.append(Message(
                role="system",
                content=f"## 岗位要求与面试官偏好\n\n{user_memory}",
            ))

        messages.append(Message(
            role="system",
            content=_build_candidate_context(session),
        ))

        if session.candidate.history_summary:
            messages.append(Message(
                role="system",
                content=f"## 候选人历史面试记录\n\n{session.candidate.history_summary}",
            ))

        return messages

    async def _eval_single(
        self,
        base_messages: list[Message],
        rounds: list[ConversationRound],
    ) -> str:
        """单次 LLM 调用路径：全量对话直接放入 user message。"""
        conversation = _format_rounds(rounds)
        messages = list(base_messages)
        messages.append(Message(
            role="user",
            content=(
                f"以下是完整的面试对话记录（共 {len(rounds)} 轮）：\n\n{conversation}\n\n"
                + _EVAL_OUTPUT_INSTRUCTIONS
            ),
        ))
        return await self._run_with_tools(messages)

    async def _eval_chunked(
        self,
        base_messages: list[Message],
        rounds: list[ConversationRound],
    ) -> str:
        """分块 map-reduce 路径：先对每块局部分析，再汇总生成完整报告。"""
        total = len(rounds)
        chunk_count = (total + _CHUNK_SIZE - 1) // _CHUNK_SIZE
        partial_analyses: list[str] = []

        # Map 阶段：逐块分析
        for i in range(0, total, _CHUNK_SIZE):
            chunk = rounds[i : i + _CHUNK_SIZE]
            start_round = chunk[0].round_number
            end_round = chunk[-1].round_number
            chunk_idx = i // _CHUNK_SIZE + 1

            messages = list(base_messages)
            messages.append(Message(
                role="user",
                content=(
                    f"以下是面试对话的第 {start_round}–{end_round} 轮"
                    f"（共 {total} 轮中的第 {chunk_idx}/{chunk_count} 段）：\n\n"
                    f"{_format_rounds(chunk)}\n\n"
                    "请分析候选人在这部分对话中的表现，输出结构化文字，包含：\n"
                    "- 每道题候选人的回答质量与深度\n"
                    "- 体现出的能力亮点（引用候选人原话）\n"
                    "- 明显的不足或知识盲点\n"
                    "- 涉及的考察维度判断"
                ),
            ))
            result = await self._run_with_tools(messages)
            partial_analyses.append(
                f"【第 {start_round}–{end_round} 轮分析】\n{result}"
            )
            logger.info(
                "EvalAgent chunked map %d/%d done rounds=%d-%d",
                chunk_idx,
                chunk_count,
                start_round,
                end_round,
            )

        # Reduce 阶段：汇总生成最终报告
        all_analyses = "\n\n".join(partial_analyses)
        messages = list(base_messages)
        messages.append(Message(
            role="user",
            content=(
                f"以下是对候选人面试各阶段的逐段分析结果（共 {total} 轮，分 {chunk_count} 段）：\n\n"
                f"{all_analyses}\n\n"
                "请综合以上所有分析，生成完整面试评价报告。\n"
                + _EVAL_OUTPUT_INSTRUCTIONS
            ),
        ))
        logger.info("EvalAgent chunked reduce phase start chunks=%d", chunk_count)
        return await self._run_with_tools(messages)


# ── module-level helpers ───────────────────────────────────────────────────────


def _format_rounds(rounds: list[ConversationRound]) -> str:
    return "\n\n".join(
        f"第 {r.round_number} 轮\n面试官: {r.interviewer_text}\n候选人: {r.candidate_text}"
        for r in rounds
    )


def _build_candidate_context(session: InterviewSession) -> str:
    candidate = session.candidate
    parts = [
        "## 候选人信息",
        f"姓名：{candidate.name}（ID: {candidate.id}）",
    ]
    if candidate.current_position:
        parts.append(f"职位：{candidate.current_position}")
    if candidate.years_of_experience is not None:
        parts.append(f"工作年限：{candidate.years_of_experience} 年")
    if candidate.skills:
        parts.append(f"技能：{', '.join(candidate.skills[:20])}")
    if candidate.resume_summary:
        parts.append(f"简历摘要：{candidate.resume_summary}")
    if session.question_plan:
        parts.append("面试题目清单：")
        for q in session.question_plan:
            mark = "✓" if q.is_covered else "○"
            parts.append(f"  {mark} [{q.dimension}] {q.question}")
    return "\n".join(parts)


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
