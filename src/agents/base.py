"""BaseAgent — 所有 Agent 的抽象基类与共享请求/响应数据结构。"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, TYPE_CHECKING

from ..framework.prompt_builder import AgentConfig, PromptBuilder
from ..framework.tool_registry import ToolRegistry
from ..models.exceptions import LLMResponseError
from ..models.message import Message
from ..models.session import InterviewSession

if TYPE_CHECKING:
    from ..llm.protocol import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class AgentRequest:
    """Agent 输入请求。

    `type` 取值见 docs/arc/agent-orchestrator.md:
      - parse_resume / generate_questions  (ResumeAgent)
      - set_trigger_mode                   (InterviewAgent 同步)
      - generate_suggestion                (InterviewAgent 流式)
      - generate_eval                      (EvalAgent)
    """

    type: str
    payload: dict
    session: InterviewSession
    request_id: int | None = None


@dataclass
class AgentResponse:
    """Agent 同步请求的统一响应。"""

    success: bool
    data: dict | None = None
    error: str | None = None


class BaseAgent(ABC):
    """所有 Agent 的抽象基类。"""

    def __init__(
        self,
        config: AgentConfig,
        prompt_builder: PromptBuilder,
        llm_client: "LLMClient",
        tool_registry: ToolRegistry,
    ) -> None:
        self.config = config
        self.prompt_builder = prompt_builder
        self.llm_client = llm_client
        self.tool_registry = tool_registry

    @abstractmethod
    async def on_activate(self, session: InterviewSession) -> None:
        """Agent 被切换为活跃状态时调用。"""

    @abstractmethod
    async def on_deactivate(self, session: InterviewSession) -> None:
        """Agent 被切换为非活跃状态时调用。"""

    @abstractmethod
    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        """处理同步请求。"""

    async def handle_stream(self, request: AgentRequest) -> AsyncIterator[str]:
        """流式返回（默认不支持，仅 InterviewAgent 覆盖）。"""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support streaming"
        )
        # 不可达，标注为 async generator
        yield ""  # pragma: no cover

    async def _run_with_tools(
        self,
        messages: list[Message],
        max_tool_rounds: int = 5,
    ) -> str:
        """LLM 调用循环：检测 tool_calls 并顺序执行，直到 LLM 输出纯文本。"""
        for _ in range(max_tool_rounds):
            tool_schemas = self.tool_registry.get_schemas(self.config.tool_names)
            response = await self.llm_client.chat(
                messages,
                tools=tool_schemas if tool_schemas else None,
            )
            if not response.tool_calls:
                return response.content or ""

            messages.append(
                Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            for tc in response.tool_calls:
                result = await self.tool_registry.dispatch(
                    tc.function.name,
                    tc.function.arguments,
                )
                messages.append(
                    Message(
                        role="tool",
                        content=result,
                        tool_call_id=tc.id,
                    )
                )

        raise LLMResponseError("工具调用轮次超出上限，可能存在循环调用")