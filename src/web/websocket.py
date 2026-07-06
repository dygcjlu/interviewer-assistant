"""WebSocket 实时通信处理器 — /ws/interview。"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from src.logging import bind_connection_id, bind_op, bind_session_id

from ..agents.base import AgentRequest
from ..models.exceptions import SessionError

logger = logging.getLogger(__name__)


async def interview_ws_handler(websocket: WebSocket, controller) -> None:
    await websocket.accept()
    connection_id = uuid.uuid4().hex[:8]
    bind_connection_id(connection_id)
    logger.info("WebSocket connected connection_id=%s", connection_id)

    # L4-6: closed 标志短路已断连的 sender；失败时主动 raise 触发 controller 内主动 detach
    sender_state = {"closed": False}

    async def ws_sender(msg: dict) -> None:
        if sender_state["closed"]:
            raise ConnectionError("ws_sender closed")
        try:
            await websocket.send_json(msg)
        except Exception as exc:
            sender_state["closed"] = True
            logger.info(
                "WebSocket send failed (marking closed) connection_id=%s type=%s err=%s",
                connection_id,
                msg.get("type"),
                exc,
            )
            raise

    controller.attach_ws_sender(ws_sender)
    conn_id = id(ws_sender)

    session = await controller.get_session()
    if session:
        bind_session_id(session.id)
        await ws_sender(
            {
                "type": "session_snapshot",
                "session_id": session.id,
                "stage": session.stage.value,
                "trigger_mode": session.metadata.trigger_mode,
                "rounds_count": len(session.rounds),
                "candidate_name": session.candidate.name or "",
                "brief": session.interview_brief,
            }
        )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("WebSocket invalid_json connection_id=%s", connection_id)
                await ws_sender(
                    {
                        "type": "error",
                        "code": "invalid_json",
                        "message": "无效 JSON",
                        "recoverable": True,
                    }
                )
                continue
            await _dispatch(msg, controller, ws_sender, connection_id)
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected connection_id=%s", connection_id)
    except Exception:
        logger.exception("WebSocket error connection_id=%s", connection_id)
    finally:
        controller.detach_ws_sender(conn_id)


async def _dispatch(msg: dict, controller, ws_sender, connection_id: str) -> None:
    msg_type = msg.get("type")
    bind_op(msg_type or "unknown")
    logger.debug("WebSocket inbound connection_id=%s type=%s", connection_id, msg_type)

    if msg_type == "request_suggestion":
        session = await controller.get_session()
        if session is None:
            await ws_sender(
                {
                    "type": "error",
                    "code": "no_session",
                    "message": "无活跃会话",
                    "recoverable": False,
                }
            )
            return
        interview_agent = controller.interview_agent
        resp = await interview_agent.handle_request(
            AgentRequest(type="trigger_suggestion", payload={}, session=session)
        )
        if not resp.success:
            await ws_sender(
                {
                    "type": "error",
                    "code": "trigger_error",
                    "message": resp.error or "",
                    "recoverable": True,
                }
            )

    elif msg_type == "set_trigger_mode":
        mode = msg.get("mode", "auto")
        session = await controller.get_session()
        if session is None:
            return
        resp = await controller.interview_agent.handle_request(
            AgentRequest(
                type="set_trigger_mode", payload={"mode": mode}, session=session
            )
        )
        if resp.success:
            session.metadata.trigger_mode = mode
            await ws_sender(
                {
                    "type": "status",
                    "stage": session.stage.value,
                    "message": f"触发模式已切换为 {mode}",
                }
            )

    elif msg_type == "switch_agent":
        # Legacy WS message — map to controller operations
        target = msg.get("target_agent", "")
        try:
            if target == "interview":
                await controller.start_interview()
            elif target == "eval":
                await controller.stop_interview()
            else:
                raise SessionError(f"不支持的目标 Agent: {target!r}")
            session = await controller.get_session()
            if session:
                await ws_sender(
                    {
                        "type": "status",
                        "stage": session.stage.value,
                        "message": f"已切换到 {target} Agent",
                    }
                )
        except SessionError as exc:
            await ws_sender(
                {
                    "type": "error",
                    "code": "session_error",
                    "message": str(exc),
                    "recoverable": False,
                }
            )

    elif msg_type == "heartbeat":
        await ws_sender({"type": "heartbeat"})

    else:
        logger.debug("WebSocket: unknown message type %r", msg_type)
