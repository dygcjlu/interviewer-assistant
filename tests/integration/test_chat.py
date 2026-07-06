"""Integration tests — POST /api/chat（SSE 流式格式）"""

from __future__ import annotations

import json

import pytest

from src.llm.protocol import StreamChunk


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_returns_sse_format(client, mock_llm):
    """响应是 text/event-stream，每行以 'data: ' 开头，结束有 [DONE]。"""
    mock_llm.push_stream(
        [
            StreamChunk(delta="你好"),
            StreamChunk(delta="，候选人情况如下"),
            StreamChunk(
                delta="",
                is_final=True,
                accumulated_content="你好，候选人情况如下",
                prompt_tokens=10,
                completion_tokens=8,
            ),
        ]
    )

    r = await client.post("/api/chat", json={"message": "介绍一下候选人"})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]

    lines = [line for line in r.text.split("\n") if line.strip()]
    data_lines = [line for line in lines if line.startswith("data: ")]
    assert len(data_lines) >= 1
    # 最后一条是 [DONE]
    assert data_lines[-1] == "data: [DONE]"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_delta_events_have_type_field(client, mock_llm):
    """delta 事件 JSON 必须含 type='delta' 和 delta 字段。"""
    mock_llm.push_stream(
        [
            StreamChunk(delta="测试内容"),
            StreamChunk(delta="", is_final=True, accumulated_content="测试内容"),
        ]
    )

    r = await client.post("/api/chat", json={"message": "测试"})
    lines = r.text.split("\n")
    delta_lines = [
        line[6:] for line in lines if line.startswith("data: ") and line != "data: [DONE]"
    ]
    for raw in delta_lines:
        event = json.loads(raw)
        assert "type" in event
        assert event["type"] in ("delta", "tool_call")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_agent_not_ready_returns_503(tmp_path):
    """main_agent 未注入时返回 503 not_ready。"""
    from contextlib import asynccontextmanager

    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from src.web.app import create_app

    @asynccontextmanager
    async def lifespan(app):
        app.state.main_agent = None
        app.state.controller = None
        app.state.memory_module = None
        app.state.context_manager = None
        app.state.settings = None
        app.state.startup_warnings = []
        yield

    app = create_app(lifespan=lifespan)
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post("/api/chat", json={"message": "hello"})
            assert r.status_code == 503
            assert r.json()["detail"]["code"] == "not_ready"
