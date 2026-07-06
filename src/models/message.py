from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FunctionCallInfo:
    name: str
    arguments: str  # JSON 字符串


@dataclass
class ToolCallInfo:
    id: str
    function: FunctionCallInfo
    type: str = "function"


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCallInfo] | None = None
    tool_call_id: str | None = None  # role="tool" 时关联的调用 ID
    reasoning_content: str | None = (
        None  # 思考模式下的推理链（DeepSeek 工具调用时必须回传）
    )
