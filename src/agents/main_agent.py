"""MainAgent — 面试官的唯一对话入口，全程常驻单例。

通过分层系统提示感知面试官偏好、候选人信息和当前会话状态；
通过工具完成对话本身无法直接执行的操作。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import AsyncIterator, Any, TYPE_CHECKING

from ..models.message import Message
from ..models.candidate import CandidateProfile
from ..storage.conversation_logger import ConversationLogger

if TYPE_CHECKING:
    from ..llm.client import OpenAICompatibleClient
    from ..framework.tool_registry import ToolRegistry
    from .interview_controller import InterviewController
    from .resume_agent import ResumeAgent
    from ..storage.memory_module import MemoryModule

logger = logging.getLogger(__name__)

_HISTORY_LIMIT = 24

_LAYER1_ROLE = """你是一位专业的面试助手 Agent，帮助面试官管理候选人、准备面试问题、支持面试流程。

你的能力：
- 与面试官自然对话，理解需求并提供建议
- 通过工具解析候选人简历、生成面试题目
- 记忆面试官的偏好和岗位要求

对话风格：
- 简洁专业，避免冗长
- 主动理解面试官意图，提供有价值的建议
- 当面试官提供岗位要求或偏好信息时，主动调用 update_user_memory 工具保存
"""


class MainAgent:
    """面试官唯一对话入口，全程常驻。"""

    def __init__(
        self,
        llm_client: "OpenAICompatibleClient",
        tool_registry: "ToolRegistry",
        memory_module: "MemoryModule",
        user_memory_path: str = "USER.md",
    ) -> None:
        self._llm = llm_client
        self._tools = tool_registry
        self._memory_module = memory_module
        self._user_memory_path = Path(user_memory_path)
        self._history: list[Message] = []

        # Lazy-bound references (set by main.py after all components are ready)
        self._resume_agent: ResumeAgent | None = None
        self._controller: InterviewController | None = None

        # System prompt layers
        self._layer2_user_memory: str = ""
        self._layer3_candidate: str = ""
        self._cached_system_prompt: str | None = None

        self._logger = ConversationLogger(Path("conversations/main_agent.jsonl"))

        self._load_user_memory()

    def bind_resume_agent(self, agent: "ResumeAgent") -> None:
        self._resume_agent = agent

    def bind_controller(self, controller: "InterviewController") -> None:
        self._controller = controller

    # ── System prompt assembly ─────────────────────────────────────────────────

    def _load_user_memory(self) -> None:
        if self._user_memory_path.exists():
            self._layer2_user_memory = self._user_memory_path.read_text(encoding="utf-8")
        else:
            self._layer2_user_memory = ""

    def reload_user_memory(self) -> None:
        self._load_user_memory()
        self._cached_system_prompt = None
        logger.info("MainAgent: reloaded USER.md (%d chars)", len(self._layer2_user_memory))

    def set_candidate_context(
        self, profile: CandidateProfile, questions: list[dict[str, Any]] | None = None
    ) -> None:
        parts = [f"\n当前候选人：{profile.name}（ID: {profile.id}）"]
        if profile.current_position:
            parts.append(f"职位：{profile.current_position}")
        if profile.years_of_experience is not None:
            parts.append(f"工作年限：{profile.years_of_experience} 年")
        if profile.skills:
            parts.append(f"技能：{', '.join(profile.skills[:15])}")
        if profile.resume_summary:
            parts.append(f"简历摘要：{profile.resume_summary}")
        if questions:
            q_lines = "\n".join(
                f"  {i+1}. [{q.get('dimension', '')}] {q.get('question', '')}"
                for i, q in enumerate(questions[:12])
            )
            parts.append(f"面试题目：\n{q_lines}")
        self._layer3_candidate = "\n".join(parts)
        self._cached_system_prompt = None
        logger.info("MainAgent: candidate context updated for %s", profile.name)

    def clear_candidate_context(self) -> None:
        self._layer3_candidate = ""
        self._cached_system_prompt = None

    def _build_system_prompt(self) -> str:
        if self._cached_system_prompt is not None:
            return self._cached_system_prompt
        sections = [_LAYER1_ROLE]
        if self._layer2_user_memory:
            sections.append(f"\n## 面试官偏好与岗位要求\n\n{self._layer2_user_memory}")
        if self._layer3_candidate:
            sections.append(f"\n## 当前候选人信息\n{self._layer3_candidate}")
        self._cached_system_prompt = "\n".join(sections)
        return self._cached_system_prompt

    # ── Tool definitions ───────────────────────────────────────────────────────

    def get_tool_names(self) -> list[str]:
        return [
            "dispatch_to_agent",
            "update_user_memory",
        ]

    # ── Core conversation method ───────────────────────────────────────────────

    async def handle_chat(self, user_message: str) -> AsyncIterator[str]:
        """处理用户消息，流式返回 LLM 回复。"""
        user_msg = Message(role="user", content=user_message)
        self._history.append(user_msg)
        new_messages: list[Message] = [user_msg]

        system_prompt = self._build_system_prompt()
        messages = [Message(role="system", content=system_prompt)]
        messages.extend(self._history)

        tool_schemas = self._tools.get_schemas(self.get_tool_names()) or None

        try:
            response = await self._llm.chat(messages=messages, tools=tool_schemas)
        except Exception as exc:
            logger.exception("MainAgent: LLM call failed")
            error_msg = f"抱歉，AI 服务暂时不可用：{exc}"
            error_assistant = Message(role="assistant", content=error_msg)
            self._history.append(error_assistant)
            new_messages.append(error_assistant)
            yield error_msg
            await self._logger.append_with_system(system_prompt, new_messages)
            self._trim_history()
            return

        # Handle tool calls
        if response.tool_calls:
            assistant_msg = Message(
                role="assistant", content=response.content, tool_calls=response.tool_calls
            )
            self._history.append(assistant_msg)
            new_messages.append(assistant_msg)

            for tc in response.tool_calls:
                result_str = await self._tools.dispatch(tc.function.name, tc.function.arguments)
                tool_msg = Message(role="tool", content=result_str, tool_call_id=tc.id)
                self._history.append(tool_msg)
                new_messages.append(tool_msg)

            # Second LLM call: use potentially-refreshed prompt (tool may have invalidated cache)
            messages2 = [Message(role="system", content=self._build_system_prompt())]
            messages2.extend(self._history)
            reply_text = ""
            try:
                async for chunk in self._llm.chat_stream(messages2):
                    if chunk.delta:
                        reply_text += chunk.delta
                        yield chunk.delta
                    if chunk.is_final:
                        break
            except Exception as exc:
                logger.exception("MainAgent: second LLM call (stream) failed")
                err = f"工具调用完成，但生成回复时出错：{exc}"
                yield err
                reply_text = err

            final_msg = Message(role="assistant", content=reply_text)
            self._history.append(final_msg)
            new_messages.append(final_msg)
        else:
            reply = response.content or ""
            reply_msg = Message(role="assistant", content=reply)
            self._history.append(reply_msg)
            new_messages.append(reply_msg)
            yield reply

        await self._logger.append_with_system(system_prompt, new_messages)
        self._trim_history()

    def _trim_history(self) -> None:
        if len(self._history) > _HISTORY_LIMIT:
            self._history = self._history[-_HISTORY_LIMIT:]
