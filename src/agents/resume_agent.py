"""ResumeAgent — 任务驱动的 ReAct 循环，负责简历解析与面试题目生成。"""

from __future__ import annotations

import json
import logging

from ..models.message import Message
from ..models.session import InterviewSession
from .base import AgentRequest, AgentResponse, BaseAgent

logger = logging.getLogger(__name__)

# L1-6: parse_resume_pdf 返回 user_facing=True 错误时，ResumeAgent 立即跳出 ReAct
# 并把错误透传出去（避免 LLM 把错误"美化"为模糊回复）。
_USER_FACING_SENTINEL = "__USER_FACING_ERROR__::"


class ResumeAgent(BaseAgent):
    """简历分析 Agent — ReAct 模式，通过工具自主完成任务。"""

    async def on_activate(self, session: InterviewSession) -> None:
        logger.info("ResumeAgent activated for session %s", session.id)

    async def on_deactivate(self, session: InterviewSession) -> None:
        logger.info("ResumeAgent deactivated for session %s", session.id)

    async def execute(self, task: str) -> dict:
        """ReAct 入口 — 由 dispatch_to_agent 工具调用。

        Args:
            task: 自然语言任务描述，例如：
                  "将 resumes/张三.pdf 解析为 Markdown 并保存为 resumes/张三.md"

        Returns:
            {"type": "parse_done", ...} | {"type": "brief_done", ...} | {"type": "error", ...}
        """
        self._bind_log_context("execute")
        from ..config import get_settings

        settings = get_settings()
        max_rounds = settings.RESUME_AGENT_MAX_TOOL_ROUNDS

        messages = self._build_messages(task)

        # L1-6: parse_resume_pdf 标记 user_facing=True 时跳出 ReAct 早退
        def _on_tool_result(name: str, result: str) -> str | None:
            if name != "parse_resume_pdf":
                return None
            try:
                parsed = json.loads(result)
            except Exception:
                return None
            if (
                isinstance(parsed, dict)
                and parsed.get("user_facing")
                and parsed.get("error")
            ):
                return _USER_FACING_SENTINEL + str(parsed["error"])
            return None

        try:
            result_text = await self._run_with_tools(
                messages,
                max_tool_rounds=max_rounds,
                on_tool_result=_on_tool_result,
            )
        except Exception as exc:
            logger.exception("ResumeAgent.execute failed task=%r", task)
            return {"type": "error", "message": str(exc)}

        # L1-6: 收到 user_facing 早退信号 → 不解析 JSON，直接返回 error 透传
        if result_text.startswith(_USER_FACING_SENTINEL):
            err_text = result_text[len(_USER_FACING_SENTINEL) :]
            logger.warning("ResumeAgent.execute user_facing early exit: %s", err_text)
            return {"type": "error", "message": err_text, "user_facing": True}

        try:
            return _extract_json(result_text)
        except json.JSONDecodeError as exc:
            # L2-4: LLM 最终输出非 JSON 时，回看 messages 中已成功的 file_write 副作用，
            # 构造伪 parse_done，避免"工作已落盘但被视为失败"。
            fallback = _fallback_from_messages(messages, task, result_text)
            if fallback is not None:
                logger.warning(
                    "ResumeAgent.execute: _extract_json failed (%s), fallback to %s",
                    exc,
                    fallback.get("type"),
                )
                fallback["fallback"] = True
                return fallback
            logger.error(
                "ResumeAgent.execute: _extract_json failed and no fallback available text=%r",
                result_text[:200],
            )
            return {"type": "error", "message": f"输出格式无法解析：{exc}"}

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        """兼容 BaseAgent 接口（不对外使用）。"""
        return AgentResponse(
            success=False, error="ResumeAgent 只通过 dispatch_to_agent 调用"
        )

    def _build_messages(self, task: str) -> list[Message]:
        import uuid
        from datetime import datetime

        from ..models.candidate import CandidateProfile
        from ..models.session import InterviewSession, InterviewStage, SessionMetadata

        dummy_session = InterviewSession(
            id=str(uuid.uuid4()),
            candidate=CandidateProfile(id=str(uuid.uuid4()), name=""),
            rounds=[],
            stage=InterviewStage.IDLE,
            context_summary="",
            interview_brief="",
            metadata=SessionMetadata(candidate_id="", start_time=datetime.now()),
        )
        messages = self.prompt_builder.build(dummy_session, self.config)
        messages.append(Message(role="user", content=task))
        return messages


def _fallback_from_messages(
    messages: list[Message], task: str, last_text: str
) -> dict | None:
    """L2-4: _extract_json 失败时回看 ReAct 历史，根据已成功的 file_write 构造伪结果。

    扫描逻辑：
    - 找最近成功的 file_write tool call
    - 根据 path 后缀判断 parse_done（.md）
    - 简报生成任务无 file_write 副作用，无法回退
    """
    file_writes: list[tuple[str, str]] = []  # (path, content)
    pending_calls: dict[str, tuple[str, str]] = {}  # tool_call_id -> (path, content)

    for msg in messages:
        if msg.role == "assistant" and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                if getattr(tc, "function", None) and tc.function.name == "file_write":
                    try:
                        args = json.loads(tc.function.arguments)
                    except Exception:
                        continue
                    path = str(args.get("path") or args.get("file_path") or "").strip()
                    content = str(args.get("content") or "")
                    if path:
                        pending_calls[tc.id] = (path, content)
        elif msg.role == "tool" and msg.tool_call_id in pending_calls:
            path, content = pending_calls.pop(msg.tool_call_id)
            file_writes.append((path, content))

    if not file_writes:
        return None

    last_path, _ = file_writes[-1]

    if last_path.endswith(".md"):
        return {
            "type": "parse_done",
            "markdown_path": last_path,
            "profile": {},
            "note": "fallback_from_file_write",
        }

    return None


def _extract_json(text: str) -> dict | list:
    """从 LLM 输出中尽力提取 JSON。容错处理 ```json 代码块包裹。"""
    if not text:
        raise json.JSONDecodeError("empty response", "", 0)
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    start_obj = text.find("{")
    start_arr = text.find("[")
    candidates = [c for c in (start_obj, start_arr) if c >= 0]
    if not candidates:
        raise json.JSONDecodeError("no JSON object found", text, 0)
    start = min(candidates)
    try:
        obj, _ = decoder.raw_decode(text, start)
        return obj
    except json.JSONDecodeError:
        end_obj = text.rfind("}")
        end_arr = text.rfind("]")
        end = max(end_obj, end_arr)
        if end > start:
            return json.loads(text[start : end + 1])
        raise json.JSONDecodeError("no valid JSON found", text, start)
