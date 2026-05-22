"""MainAgent — 面试官的唯一对话入口，全程常驻单例。

通过分层系统提示感知面试官偏好、候选人信息和当前会话状态；
通过工具完成对话本身无法直接执行的操作。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Any, TYPE_CHECKING

from ..models.message import Message
from ..models.candidate import CandidateProfile
from ..storage.conversation_logger import ConversationLogger
from ..storage.user_memory import UserMemoryStore

if TYPE_CHECKING:
    from pathlib import Path
    from ..llm.client import OpenAICompatibleClient
    from ..framework.tool_registry import ToolRegistry
    from .interview_controller import InterviewController
    from .resume_agent import ResumeAgent
    from ..storage.memory_module import MemoryModule

logger = logging.getLogger(__name__)

_HISTORY_LIMIT = 24
_NUDGE_INTERVAL = 10  # 每隔多少轮触发一次后台记忆审查（0 表示禁用）
_NUDGE_MAX_ITER = 3   # 后台审查最多迭代次数

_LAYER1_ROLE = """你是一位专业的面试助手 Agent，帮助面试官管理候选人、准备面试问题、支持面试流程。

你的能力：
- 与面试官自然对话，理解需求并提供建议
- 通过工具解析候选人简历、生成面试题目
- 记忆面试官的偏好和岗位要求

对话风格：
- 简洁专业，避免冗长
- 主动理解面试官意图，提供有价值的建议
- 当面试官提供岗位要求或偏好信息时，主动调用 manage_user_memory 工具保存
"""

_NUDGE_SYSTEM = """你是一个记忆整理助手。请回顾以下对话，判断是否有新的岗位要求、面试偏好或重要信息需要保存到面试官记忆。

注意：
- 只保存面试官明确表达的、具有长期参考价值的信息（岗位要求、技术栈偏好、面试风格等）
- 若已有相似条目，使用 replace 更新而非重复 add
- 若无值得保存的内容，不要调用任何工具，直接结束
"""


class MainAgent:
    """面试官唯一对话入口，全程常驻。"""

    def __init__(
        self,
        llm_client: "OpenAICompatibleClient",
        tool_registry: "ToolRegistry",
        memory_module: "MemoryModule",
        user_memory_store: UserMemoryStore,
    ) -> None:
        self._llm = llm_client
        self._tools = tool_registry
        self._memory_module = memory_module
        self._user_memory_store = user_memory_store
        self._history: list[Message] = []

        # Lazy-bound references (set by main.py after all components are ready)
        self._resume_agent: ResumeAgent | None = None
        self._controller: InterviewController | None = None

        # System prompt layers
        self._layer2_user_memory: str = ""
        self._layer3_candidate: str = ""
        self._cached_system_prompt: str | None = None

        # Memory nudge state
        self._turns_since_nudge: int = 0
        self._should_nudge: bool = False
        self._nudge_task: asyncio.Task | None = None

        from pathlib import Path
        self._logger = ConversationLogger(Path("conversations/main_agent.jsonl"))

        self._load_user_memory()

    def bind_resume_agent(self, agent: "ResumeAgent") -> None:
        self._resume_agent = agent

    def bind_controller(self, controller: "InterviewController") -> None:
        self._controller = controller

    # ── System prompt assembly ─────────────────────────────────────────────────

    def _load_user_memory(self) -> None:
        self._layer2_user_memory = self._user_memory_store.render()

    def reload_user_memory(self) -> None:
        """记忆更新后刷新（store 已是最新，无需重读磁盘）。"""
        self._load_user_memory()
        self._cached_system_prompt = None
        logger.info("MainAgent: reloaded user memory (%d chars)", len(self._layer2_user_memory))

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
            "manage_user_memory",
        ]

    # ── Core conversation method ───────────────────────────────────────────────

    async def handle_chat(self, user_message: str) -> AsyncIterator[str]:
        """处理用户消息，流式返回 LLM 回复。"""
        # Nudge 计数
        if _NUDGE_INTERVAL > 0:
            self._turns_since_nudge += 1
            if self._turns_since_nudge >= _NUDGE_INTERVAL:
                self._should_nudge = True
                self._turns_since_nudge = 0

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
        tool_called_memory = False
        if response.tool_calls:
            assistant_msg = Message(
                role="assistant", content=response.content, tool_calls=response.tool_calls
            )
            self._history.append(assistant_msg)
            new_messages.append(assistant_msg)

            for tc in response.tool_calls:
                if tc.function.name == "manage_user_memory":
                    tool_called_memory = True
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

        # LLM 主动调用了记忆工具，重置 nudge 计数
        if tool_called_memory:
            self._turns_since_nudge = 0
            self._should_nudge = False

        await self._logger.append_with_system(system_prompt, new_messages)
        self._trim_history()

        # 触发后台记忆审查（不阻塞当前轮次；跳过若上一次尚未完成）
        if self._should_nudge and not tool_called_memory:
            self._should_nudge = False
            if self._nudge_task is None or self._nudge_task.done():
                self._nudge_task = asyncio.create_task(
                    self._background_memory_review()
                )

    def _trim_history(self) -> None:
        if len(self._history) > _HISTORY_LIMIT:
            self._history = self._history[-_HISTORY_LIMIT:]

    # ── Memory nudge ───────────────────────────────────────────────────────────

    async def _background_memory_review(self) -> None:
        """后台记忆审查：检查最近对话，若有值得保存的信息则调用 manage_user_memory。"""
        logger.info("MainAgent: background memory review triggered")
        try:
            recent = self._history[-12:]  # 最近 ~6 轮
            messages: list[Message] = [Message(role="system", content=_NUDGE_SYSTEM)]
            messages.extend(recent)

            nudge_tool_names = ["manage_user_memory"]
            tool_schemas = self._tools.get_schemas(nudge_tool_names) or None

            for iteration in range(_NUDGE_MAX_ITER):
                response = await self._llm.chat(messages=messages, tools=tool_schemas)

                if not response.tool_calls:
                    logger.info(
                        "MainAgent: background review finished after %d iter (no tool call)",
                        iteration + 1,
                    )
                    break

                messages.append(
                    Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
                )
                for tc in response.tool_calls:
                    result_str = await self._tools.dispatch(tc.function.name, tc.function.arguments)
                    messages.append(Message(role="tool", content=result_str, tool_call_id=tc.id))
                    logger.info(
                        "MainAgent: background review called %s -> %s",
                        tc.function.name,
                        result_str[:120],
                    )
            else:
                logger.warning("MainAgent: background review reached max iterations (%d)", _NUDGE_MAX_ITER)

        except Exception:
            logger.exception("MainAgent: background memory review failed (ignored)")
