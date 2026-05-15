"""
完成标准：
- LLMClient Protocol 可被正确实现（duck typing 通过）
- ChatResponse 字段类型正确
- StreamChunk is_final=True 时包含 token 统计
"""
import pytest
from src.models.message import Message
from src.llm.protocol import ChatResponse, StreamChunk, ToolSchema, ToolFunction


def test_chat_response_defaults():
    resp = ChatResponse(content="hello")
    assert resp.tool_calls is None
    assert resp.prompt_tokens == 0
    assert resp.completion_tokens == 0


def test_stream_chunk_final():
    chunk = StreamChunk(delta="end", is_final=True, prompt_tokens=10, completion_tokens=5)
    assert chunk.is_final is True
    assert chunk.prompt_tokens == 10


def test_tool_schema_type_default():
    schema = ToolSchema(function=ToolFunction(name="f", description="d", parameters={}))
    assert schema.type == "function"
