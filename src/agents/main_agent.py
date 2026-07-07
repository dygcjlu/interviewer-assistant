"""MainAgent — 面试官的唯一对话入口，全程常驻单例。

通过分层系统提示感知面试官偏好、候选人信息和当前会话状态；
通过工具完成对话本身无法直接执行的操作。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from src.logging import bind_op

from ..models.candidate import CandidateProfile
from ..models.message import Message
from ..storage.conversation_logger import ConversationLogger
from ..storage.user_memory import UserMemoryStore

if TYPE_CHECKING:
    from ..framework.tool_registry import ToolRegistry
    from ..llm.client import OpenAICompatibleClient
    from ..storage.memory_module import MemoryModule

logger = logging.getLogger(__name__)

_HISTORY_LIMIT = 24
_NUDGE_INTERVAL = 10  # 每隔多少轮触发一次后台记忆审查（0 表示禁用）
_NUDGE_MAX_ITER = 3  # 后台审查最多迭代次数
_TOOL_LOOP_MAX_ROUNDS = 5  # MainAgent.handle_chat 中工具调用循环最大轮次


def _extract_user_facing_error(tool_result: str) -> str | None:
    """L1-6: 从工具结果 JSON 中抽取 user_facing 错误文本。

    支持两种形态：
    - 直接：{"error": "...", "user_facing": True}
    - dispatch_to_agent 包装：{"type": "error", "message": "...", "user_facing": True}
    """
    if not tool_result or "user_facing" not in tool_result:
        # 快速短路：未含关键字直接返回（避免对每个 tool result 都 json.loads）
        return None
    try:
        data = json.loads(tool_result)
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("user_facing"):
        return None
    return str(data.get("message") or data.get("error") or "")


def _extract_duplicate_candidate_event(tool_result: str) -> dict | None:
    """检测 dispatch_to_agent 返回结果里的 duplicate_candidate 标记（parse_done 判重命中）。"""
    if not tool_result or "duplicate_candidate" not in tool_result:
        return None
    try:
        data = json.loads(tool_result)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    dup = data.get("duplicate_candidate")
    if not isinstance(dup, dict):
        return None
    return {"type": "duplicate_candidate", **dup}


_LAYER1_ROLE = """你是一位专业的面试助手 Agent，帮助面试官管理候选人、准备面试、支持面试流程。

你的能力：
- 与面试官自然对话，理解需求并提供建议
- 通过 dispatch_to_agent 工具委托 ResumeAgent 解析简历、生成面试简报
- 记忆面试官的偏好和岗位要求

## 简历解析后工作流

简历解析完成后，进入两阶段面试准备流程：

**阶段一：候选人分析呈现**
- 主动呈现候选人概况（背景、年限、职位）
- 标注风险信号（如频繁跳槽、经历断层、技能与岗位不符等）并说明理由
- 建议重点关注方向（基于简历内容和已知岗位要求）

**阶段二：收集面试官关注点**
- 通过 2-4 轮对话收集面试官的具体关注点（如"重点考察稳定性"、"关注系统设计能力"）
- 面试官确认后，主动提议生成面试简报
- 用户确认后调用：`dispatch_to_agent(agent="resume", task="为候选人[ID]生成面试简报，关注点：[整理后内容]...")`

## 工具使用规则

- 解析简历、生成面试简报必须调用 dispatch_to_agent(agent="resume", ...)，不要自行在对话中输出完整简报内容
- 简报生成后同步到「简报」面板；工具完成后用简短文字总结即可

## 对话风格

- 简洁专业，避免冗长
- 主动理解面试官意图，提供有价值的建议
- 当面试官提供岗位要求或偏好信息时，主动调用 manage_user_memory 工具保存
- **不应保存**：候选人个人信息（姓名、简历内容、面试表现、回答质量等）——这些属于候选人档案，由 dispatch_to_agent 持久化管理
"""

_NUDGE_SYSTEM = """你是一个记忆整理助手。请回顾以下对话，判断是否有新的岗位要求、面试偏好或重要信息需要保存到面试官记忆。

注意：
- 只保存面试官明确表达的、具有长期参考价值的信息（岗位要求、技术栈偏好、面试风格等）
- **忽略候选人具体表现**：候选人的回答内容、评价、能力判断等不应保存到面试官记忆
- 若已有相似条目，使用 replace 更新而非重复 add
- 若无值得保存的内容，不要调用任何工具，直接结束
"""


class MainAgent:
    """面试官唯一对话入口，全程常驻。"""

    def __init__(
        self,
        llm_client: OpenAICompatibleClient,
        tool_registry: ToolRegistry,
        memory_module: MemoryModule,
        user_memory_store: UserMemoryStore,
    ) -> None:
        self._llm = llm_client
        self._tools = tool_registry
        self._memory_module = memory_module
        self._user_memory_store = user_memory_store
        self._history: list[Message] = []

        # System prompt layers
        self._layer2_user_memory: str = ""
        self._layer3_candidate: str = ""
        self._cached_system_prompt: str | None = None

        # Memory nudge state
        self._turns_since_nudge: int = 0
        self._should_nudge: bool = False
        self._nudge_task: asyncio.Task | None = None

        # 串行化 handle_chat：防止并发请求撞坏 _history / nudge 计数
        self._chat_lock = asyncio.Lock()

        from pathlib import Path

        self._logger = ConversationLogger(Path("conversations/main_agent.jsonl"))

        self._load_user_memory()

    def _bind_log_context(self, op: str | None = None) -> None:
        if op is not None:
            bind_op(op)

    # ── System prompt assembly ─────────────────────────────────────────────────

    def _load_user_memory(self) -> None:
        self._layer2_user_memory = self._user_memory_store.render()

    def reload_user_memory(self) -> None:
        """记忆更新后刷新（store 已是最新，无需重读磁盘）。"""
        self._load_user_memory()
        self._cached_system_prompt = None
        logger.info(
            "MainAgent: reloaded user memory (%d chars)", len(self._layer2_user_memory)
        )

    def set_candidate_context(
        self,
        profile: CandidateProfile,
        interview_brief: str | None = None,
        history_summary: str | None = None,
    ) -> None:
        parts = [f"\n当前候选人：{profile.name}（ID: {profile.id}）"]
        if profile.current_position:
            parts.append(f"职位：{profile.current_position}")
        if profile.years_of_experience is not None:
            parts.append(f"工作年限：{profile.years_of_experience} 年")
        if profile.skills:
            parts.append(f"技能：{', '.join(profile.skills[:15])}")
        if profile.resume_content:
            parts.append(f"简历内容：\n{profile.resume_content[:1500]}")
        if interview_brief:
            parts.append(f"面试简报（前800字）：\n{interview_brief[:800]}")
        if history_summary:
            parts.append(f"历史面试记录：\n{history_summary[:1200]}")
        self._layer3_candidate = "\n".join(parts)
        self._cached_system_prompt = None
        logger.info("MainAgent: candidate context updated for %s", profile.name)

    def clear_candidate_context(self) -> None:
        self._layer3_candidate = ""
        self._cached_system_prompt = None

    def _build_system_prompt(self) -> str:
        if self._cached_system_prompt is None:
            sections = [_LAYER1_ROLE]
            if self._layer2_user_memory:
                sections.append(
                    f"\n## 面试官偏好与岗位要求\n\n{self._layer2_user_memory}"
                )
            if self._layer3_candidate:
                sections.append(f"\n## 当前候选人信息\n{self._layer3_candidate}")
            self._cached_system_prompt = "\n".join(sections)
        from datetime import date

        today = date.today().strftime("%Y-%m-%d")
        # Append date at the end to preserve stable prefix for LLM prompt caching.
        return f"{self._cached_system_prompt}\n\n当前日期：{today}"

    # ── Tool definitions ───────────────────────────────────────────────────────

    def get_tool_names(self) -> list[str]:
        return [
            "dispatch_to_agent",
            "manage_user_memory",
        ]

    # ── Core conversation method ───────────────────────────────────────────────

    async def handle_chat(self, user_message: str) -> AsyncIterator[str | dict]:
        """处理用户消息（用 `_chat_lock` 串行化，避免并发撞坏 `_history`），流式返回 LLM 回复。

        yield str  → 文字 delta，前端直接追加到气泡
        yield dict → 结构化事件，目前支持 {"type": "tool_call", "name": ..., "args": ...}
        """
        self._bind_log_context("chat")
        async with self._chat_lock:
            async for chunk in self._handle_chat_locked(user_message):
                yield chunk

    async def _handle_chat_locked(self, user_message: str) -> AsyncIterator[str | dict]:
        """实际对话逻辑，调用方必须已持有 `_chat_lock`。"""
        # Nudge 计数
        if _NUDGE_INTERVAL > 0:
            self._turns_since_nudge += 1
            if self._turns_since_nudge >= _NUDGE_INTERVAL:
                self._should_nudge = True
                self._turns_since_nudge = 0

        user_msg = Message(role="user", content=user_message)
        self._history.append(user_msg)
        new_messages: list[Message] = [user_msg]

        tool_called_memory = False

        system_prompt = self._build_system_prompt()
        messages = [Message(role="system", content=system_prompt)]
        messages.extend(self._history)

        tool_schemas = self._tools.get_schemas(self.get_tool_names()) or None

        # 第一次调用：用 chat_stream(with tools)
        # - LLM 返回纯文字 → delta 逐字推送（路径①）
        # - LLM 决定调用工具 → final chunk 携带 tool_calls，文字 delta 为空（路径②）
        content_acc = ""
        first_tool_calls = None
        try:
            async for chunk in self._llm.chat_stream(
                messages=messages, tools=tool_schemas
            ):
                if chunk.is_final:
                    first_tool_calls = chunk.tool_calls
                    content_acc = chunk.accumulated_content
                elif chunk.delta and not first_tool_calls:
                    # 只有确认没有 tool_calls 时才推送文字 delta
                    # 注意：tool_calls 出现时 delta 通常为空，此判断作为保险
                    content_acc += chunk.delta
                    yield chunk.delta
        except Exception as exc:
            logger.exception("MainAgent: first LLM stream call failed")
            error_msg = f"抱歉，AI 服务暂时不可用：{exc}"
            error_assistant = Message(role="assistant", content=error_msg)
            self._history.append(error_assistant)
            new_messages.append(error_assistant)
            yield error_msg
            await self._logger.append_with_system(system_prompt, new_messages)
            self._trim_history()
            return

        # 路径①：无工具调用，流式已完成，收尾
        if not first_tool_calls:
            reply_msg = Message(role="assistant", content=content_acc)
            self._history.append(reply_msg)
            new_messages.append(reply_msg)
            await self._logger.append_with_system(system_prompt, new_messages)
            self._trim_history()
            if self._should_nudge and not tool_called_memory:
                self._should_nudge = False
                if self._nudge_task is None or self._nudge_task.done():
                    self._nudge_task = asyncio.create_task(
                        self._background_memory_review()
                    )
            return

        # 路径②：有工具调用，进入工具循环
        assistant_msg = Message(
            role="assistant",
            content=content_acc or None,
            tool_calls=first_tool_calls,
        )
        self._history.append(assistant_msg)
        new_messages.append(assistant_msg)

        current_tool_calls = first_tool_calls
        loop_rounds = 0

        user_facing_error: str | None = None
        duplicate_event: dict | None = None
        while current_tool_calls and loop_rounds < _TOOL_LOOP_MAX_ROUNDS:
            loop_rounds += 1

            for tc in current_tool_calls:
                if tc.function.name == "manage_user_memory":
                    tool_called_memory = True
                # 通知前端工具调用开始
                yield {
                    "type": "tool_call",
                    "name": tc.function.name,
                    "args": tc.function.arguments,
                }
                result_str = await self._tools.dispatch(
                    tc.function.name, tc.function.arguments
                )
                tool_msg = Message(role="tool", content=result_str, tool_call_id=tc.id)
                self._history.append(tool_msg)
                new_messages.append(tool_msg)
                if user_facing_error is None:
                    user_facing_error = _extract_user_facing_error(result_str)
                if duplicate_event is None:
                    duplicate_event = _extract_duplicate_candidate_event(result_str)

            if user_facing_error is not None:
                logger.warning(
                    "MainAgent: tool returned user_facing error, short-circuiting ReAct: %s",
                    user_facing_error[:200],
                )
                break
            if duplicate_event is not None:
                # parse_done 判重命中：等待面试官在前端三选一决议，跳过继续调用 LLM
                break

            # 下一轮：检查是否还需继续调用工具（非流式）
            messages_next = [
                Message(role="system", content=self._build_system_prompt())
            ]
            messages_next.extend(self._history)
            try:
                next_resp = await self._llm.chat(
                    messages=messages_next, tools=tool_schemas
                )
            except Exception as exc:
                logger.exception(
                    "MainAgent: follow-up LLM call failed at round %d", loop_rounds
                )
                err = f"工具调用完成，但继续生成回复时出错：{exc}"
                err_msg = Message(role="assistant", content=err)
                self._history.append(err_msg)
                new_messages.append(err_msg)
                yield err
                current_tool_calls = None
                break

            if next_resp.tool_calls:
                # 还有工具调用，把 assistant 消息加入 history 继续循环
                next_assistant = Message(
                    role="assistant",
                    content=next_resp.content or None,
                    tool_calls=next_resp.tool_calls,
                )
                self._history.append(next_assistant)
                new_messages.append(next_assistant)
                current_tool_calls = next_resp.tool_calls
            else:
                current_tool_calls = None
                # 无更多工具调用，把此次文本回复加入 history
                if next_resp.content:
                    next_msg = Message(role="assistant", content=next_resp.content)
                    self._history.append(next_msg)
                    new_messages.append(next_msg)
                break

        if user_facing_error is not None:
            # L1-6: 工具层返回 user_facing 错误 → 直接呈现给用户，跳过 LLM 自由发挥
            err_msg = Message(role="assistant", content=user_facing_error)
            self._history.append(err_msg)
            new_messages.append(err_msg)
            yield user_facing_error
        elif duplicate_event is not None:
            # parse_done 判重命中 → 结构化事件 + 一句人话提示，跳过 LLM 自由发挥，
            # 等待前端弹窗决议后调用 POST /api/resume/resolve-duplicate
            assistant_text = (
                f"检测到候选人「{duplicate_event['new_name']}」与已有候选人"
                f"「{duplicate_event['existing_candidate_name']}」重名，请选择处理方式。"
            )
            dup_msg = Message(role="assistant", content=assistant_text)
            self._history.append(dup_msg)
            new_messages.append(dup_msg)
            yield duplicate_event
            yield assistant_text
        elif current_tool_calls is None and loop_rounds > 0:
            # 工具循环正常结束，检查 history 里最后一条 assistant 消息是否已有内容
            last = self._history[-1] if self._history else None
            if last and last.role == "assistant" and last.content:
                # 下一轮 LLM 已返回文字（在循环内加入 history），直接流式输出
                # 实际上此处 content 已存储，对用户 yield 即可
                # （若要真流式可再调 chat_stream，但内容已生成，复用更经济）
                pass  # already yielded nothing, fall through to final stream
        elif current_tool_calls:
            # 触达最大循环次数仍有未执行的 tool_calls
            logger.warning(
                "MainAgent: tool loop reached max rounds (%d), dropping remaining tool_calls",
                _TOOL_LOOP_MAX_ROUNDS,
            )
            warn = "（达到工具调用上限，停止迭代）"
            warn_msg = Message(role="assistant", content=warn)
            self._history.append(warn_msg)
            new_messages.append(warn_msg)
            yield warn

        # 工具循环结束后，流式输出最终回复（若上面没有提前 yield 完整回复）
        if (
            user_facing_error is None
            and duplicate_event is None
            and loop_rounds > 0
            and not current_tool_calls
        ):
            # 检查最后一条 assistant 消息是否已 yield 过
            last = self._history[-1] if self._history else None
            need_final_stream = not (last and last.role == "assistant" and last.content)
            if need_final_stream:
                messages_final = [
                    Message(role="system", content=self._build_system_prompt())
                ]
                messages_final.extend(self._history)
                reply_text = ""
                try:
                    async for chunk in self._llm.chat_stream(messages_final):
                        if chunk.delta:
                            reply_text += chunk.delta
                            yield chunk.delta
                        if chunk.is_final:
                            break
                except Exception as exc:
                    logger.exception("MainAgent: final stream call failed")
                    err = f"工具调用完成，但生成回复时出错：{exc}"
                    yield err
                    reply_text = reply_text or err

                final_msg = Message(role="assistant", content=reply_text)
                self._history.append(final_msg)
                new_messages.append(final_msg)
            else:
                # 最后一条 assistant 已有内容，直接 yield
                assert last is not None
                yield last.content or ""

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
                self._nudge_task = asyncio.create_task(self._background_memory_review())

    def _trim_history(self) -> None:
        if len(self._history) <= _HISTORY_LIMIT:
            return
        trimmed = self._history[-_HISTORY_LIMIT:]
        # 跳过截断后开头的孤儿 tool 消息（其对应的 assistant tool_call 已被截掉）
        while trimmed and trimmed[0].role == "tool":
            trimmed = trimmed[1:]
        self._history = trimmed

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
                    Message(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                )
                for tc in response.tool_calls:
                    result_str = await self._tools.dispatch(
                        tc.function.name, tc.function.arguments
                    )
                    messages.append(
                        Message(role="tool", content=result_str, tool_call_id=tc.id)
                    )
                    logger.info(
                        "MainAgent: background review called %s -> %s",
                        tc.function.name,
                        result_str[:120],
                    )
            else:
                logger.warning(
                    "MainAgent: background review reached max iterations (%d)",
                    _NUDGE_MAX_ITER,
                )

        except Exception:
            logger.exception("MainAgent: background memory review failed (ignored)")
