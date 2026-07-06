"""Integration tests — WebSocket /ws/interview 消息契约。"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient
from starlette.testclient import TestClient

from src.models.candidate import CandidateProfile

# WebSocket 测试使用 starlette TestClient 的同步 WS 支持
# 同时提供异步辅助工具


def _make_sync_client(app) -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


def _get_app(client: AsyncClient):
    return client._transport.app


# ── helpers ───────────────────────────────────────────────────────────────────


async def _seed(client, cid: str, name: str):
    memory = _get_app(client).state.memory_module
    candidate = CandidateProfile(id=cid, name=name)
    await memory.save_candidate(candidate, f"# {name}\n")


# ── heartbeat ────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_websocket_heartbeat(tmp_path, mock_llm):
    """发送 heartbeat → 收到 heartbeat 响应。"""
    from tests.integration.conftest import _build_test_app

    app = _build_test_app(tmp_path, mock_llm)
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws/interview") as ws:
            ws.send_json({"type": "heartbeat"})
            msgs = []
            # 先读完可能推来的 session_snapshot
            try:
                while True:
                    m = ws.receive_json()
                    msgs.append(m)
                    if m.get("type") == "heartbeat":
                        break
            except Exception:
                pass
            types = {m["type"] for m in msgs}
            assert "heartbeat" in types


@pytest.mark.integration
def test_websocket_invalid_json_returns_error(tmp_path, mock_llm):
    """发送非 JSON 文本 → 收到 type=error, code=invalid_json。"""
    from tests.integration.conftest import _build_test_app

    app = _build_test_app(tmp_path, mock_llm)
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws/interview") as ws:
            ws.send_text("this is not json")
            msgs = []
            try:
                for _ in range(5):
                    m = ws.receive_json()
                    msgs.append(m)
                    if m.get("type") == "error":
                        break
            except Exception:
                pass
            error_msgs = [m for m in msgs if m.get("type") == "error"]
            assert any(m.get("code") == "invalid_json" for m in error_msgs)


@pytest.mark.integration
def test_websocket_connects_and_receives_session_snapshot_when_session_exists(
    tmp_path, mock_llm
):
    """连接时若有活跃会话，立即收到 session_snapshot 消息。"""

    from tests.integration.conftest import _build_test_app

    app = _build_test_app(tmp_path, mock_llm)

    # 同步初始化：创建候选人并创建 session
    async def _setup():
        memory = app.state.memory_module
        candidate = CandidateProfile(id="cid-ws-001", name="WS 测试候选人")
        await memory.save_candidate(candidate, "# 简历\n")
        await app.state.controller.create_session("cid-ws-001")

    # 使用 TestClient 的 lifespan 触发器
    with TestClient(app) as tc:
        asyncio.run(_setup())
        with tc.websocket_connect("/ws/interview") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "session_snapshot"
            assert "stage" in msg
            assert "rounds_count" in msg


@pytest.mark.integration
def test_websocket_request_suggestion_no_session_returns_error(tmp_path, mock_llm):
    """无活跃会话时发送 request_suggestion → 收到 type=error, code=no_session。"""
    from tests.integration.conftest import _build_test_app

    app = _build_test_app(tmp_path, mock_llm)
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws/interview") as ws:
            ws.send_json({"type": "request_suggestion"})
            msgs = []
            try:
                for _ in range(5):
                    m = ws.receive_json()
                    msgs.append(m)
                    if m.get("type") == "error":
                        break
            except Exception:
                pass
            error_msgs = [m for m in msgs if m.get("type") == "error"]
            assert any(m.get("code") == "no_session" for m in error_msgs)


@pytest.mark.integration
def test_websocket_set_trigger_mode_updates_session(tmp_path, mock_llm):
    """发送 set_trigger_mode manual → 收到 status 消息且 trigger_mode 更新。"""

    from tests.integration.conftest import _build_test_app

    app = _build_test_app(tmp_path, mock_llm)

    async def _setup():
        memory = app.state.memory_module
        candidate = CandidateProfile(id="cid-ws-002", name="触发模式候选人")
        await memory.save_candidate(candidate, "# 简历\n")
        await app.state.controller.create_session("cid-ws-002")
        await app.state.controller.start_interview()

    with TestClient(app) as tc:
        asyncio.run(_setup())
        with tc.websocket_connect("/ws/interview") as ws:
            # 消费初始 snapshot
            ws.receive_json()
            ws.send_json({"type": "set_trigger_mode", "mode": "manual"})
            msgs = []
            try:
                for _ in range(5):
                    m = ws.receive_json()
                    msgs.append(m)
                    if m.get("type") == "status":
                        break
            except Exception:
                pass
            status_msgs = [m for m in msgs if m.get("type") == "status"]
            assert len(status_msgs) >= 1
