"""WebSocket 实时通信处理器 — /ws/interview。"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import WebSocket, WebSocketDisconnect

from ..agents.base import AgentRequest
from ..models.exceptions import SessionError

logger = logging.getLogger(__name__)


async def interview_ws_handler(websocket: WebSocket, orchestrator) -> None:
    await websocket.accept()

    async def ws_sender(msg: dict) -> None:
        try:
            await websocket.send_json(msg)
        except Exception:
            logger.debug("WebSocket: send failed (client disconnected)")

    orchestrator.attach_ws_sender(ws_sender)

    session = await orchestrator.get_session()
    if session:
        await ws_sender({
            "type": "session_snapshot",
            "session_id": session.id,
            "stage": session.stage.value,
            "trigger_mode": session.metadata.trigger_mode,
            "rounds_count": len(session.rounds),
        })

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws_sender({"type": "error", "code": "invalid_json", "message": "无效 JSON", "recoverable": True})
                continue
            await _dispatch(msg, orchestrator, ws_sender)
    except WebSocketDisconnect:
        logger.info("WebSocket: client disconnected")
    except Exception:
        logger.exception("WebSocket: unexpected error")
    finally:
        orchestrator.attach_ws_sender(None)


async def _dispatch(msg: dict, orchestrator, ws_sender) -> None:
    msg_type = msg.get("type")

    if msg_type == "request_suggestion":
        session = await orchestrator.get_session()
        if session is None:
            await ws_sender({"type": "error", "code": "no_session", "message": "无活跃会话", "recoverable": False})
            return
        resp = await orchestrator.handle_request(
            AgentRequest(type="trigger_suggestion", payload={}, session=session)
        )
        if not resp.success:
            await ws_sender({"type": "error", "code": "trigger_error", "message": resp.error or "", "recoverable": True})

    elif msg_type == "manual_input":
        source = msg.get("source", "interviewer")
        text = msg.get("text", "")
        session = await orchestrator.get_session()
        if session is None or not text:
            return
        from ..audio.protocol import TranscriptSegment
        tm = orchestrator.transcription_manager
        if tm is not None:
            segment = TranscriptSegment(source=source, text=text, is_final=True, timestamp=datetime.now())
            await tm.on_segment(segment)

    elif msg_type == "set_trigger_mode":
        mode = msg.get("mode", "auto")
        session = await orchestrator.get_session()
        if session is None:
            return
        resp = await orchestrator.handle_request(
            AgentRequest(type="set_trigger_mode", payload={"mode": mode}, session=session)
        )
        if resp.success:
            session.metadata.trigger_mode = mode
            await ws_sender({"type": "status", "stage": session.stage.value, "message": f"触发模式已切换为 {mode}"})

    elif msg_type == "switch_agent":
        target = msg.get("target_agent", "")
        try:
            await orchestrator.switch_agent(target, ws_sender)
        except SessionError as exc:
            await ws_sender({"type": "error", "code": "session_error", "message": str(exc), "recoverable": False})
        else:
            session = await orchestrator.get_session()
            if session:
                await ws_sender({"type": "status", "stage": session.stage.value, "message": f"已切换到 {target} Agent"})

    elif msg_type == "heartbeat":
        await ws_sender({"type": "heartbeat"})

    else:
        logger.debug("WebSocket: unknown message type %r", msg_type)