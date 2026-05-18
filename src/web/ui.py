"""NiceGUI 单页面 Agent 对话界面。

通过 set_dependencies() 在 startup 时注入运行时依赖，
使用 @ui.page("/") 在导入时注册页面路由。
"""
from __future__ import annotations

import asyncio
import json
import logging
from asyncio import Queue
from typing import Any

import httpx
from nicegui import ui
from websockets.asyncio.client import connect as ws_connect

from ..models.message import Message
from ..tools import interview_control_tools as _ict

logger = logging.getLogger(__name__)

# ── Module-level dependencies (injected by main.py at startup) ────────────────

_llm_client: Any = None
_tool_registry: Any = None
_base_url: str = "http://127.0.0.1:8000"
_ws_url: str = "ws://127.0.0.1:8000/ws/interview"

_UI_AGENT_SYSTEM = (
    "你是面试助手 Agent。根据用户自然语言指令调用合适的工具："
    "开始面试→start_interview；结束面试→stop_interview；"
    "获取/查看报告→get_eval_report；追问/建议→request_suggestion；"
    "重新提炼题目→regenerate_questions。"
    "无法匹配工具时直接以文本回复，不要强行调用工具。"
)

_STAGE_COLORS = {
    "idle": "grey",
    "resume_analysis": "blue",
    "interviewing": "green",
    "evaluating": "orange",
    "completed": "purple",
}


def set_dependencies(orchestrator: Any, memory_module: Any, llm_client: Any,
                     tool_registry: Any, settings: Any) -> None:
    global _llm_client, _tool_registry, _base_url, _ws_url
    _llm_client = llm_client
    _tool_registry = tool_registry
    _base_url = f"http://{settings.HOST}:{settings.PORT}"
    _ws_url = f"ws://{settings.HOST}:{settings.PORT}/ws/interview"

    _ict._set_base_url(_base_url)
    _ict.register_tools(tool_registry)


# ── Page registration ─────────────────────────────────────────────────────────

@ui.page("/")
async def index() -> None:
    """Main Agent dialog page — one instance per browser connection."""

    # Per-page state
    state: dict[str, Any] = {
        "candidate_id": None,
        "candidate_name": "—",
        "stage": "idle",
        "round_count": 0,
        "suggestion_label": None,   # ui.label inside streaming card
        "suggestion_text": "",
        "suggestion_card": None,    # ui.card for streaming bubble
        "agent_history": [],        # LLM conversation history
        "candidates": [],           # 缓存候选人列表供下拉框使用
    }
    recv_queue: Queue[dict] = Queue()
    send_queue: Queue[str] = Queue()

    # ── Layout ────────────────────────────────────────────────────────────────
    ui.query("body").style("margin:0; overflow:hidden")

    with ui.column().classes("w-full h-screen gap-0"):
        # Top status bar
        with ui.row().classes(
            "w-full items-center px-4 py-2 bg-white shadow-sm border-b gap-3 flex-shrink-0"
        ):
            stage_badge = ui.badge("idle").props("rounded color=grey")
            candidate_label = ui.label("候选人：—").classes("text-sm font-semibold")
            round_label = ui.label("轮次：0").classes("text-sm text-grey-6")
            ui.space()
            start_btn = ui.button("开始面试", icon="play_arrow").props(
                "flat dense color=positive"
            )
            stop_btn = ui.button("结束面试", icon="stop").props(
                "flat dense color=negative"
            )
            ws_icon = ui.icon("wifi_off").classes("text-base text-grey-5")

        # Main body
        with ui.row().classes("flex-1 w-full overflow-hidden"):
            # Chat area — 60%
            with ui.column().classes("h-full border-r overflow-hidden").style("width:60%"):
                chat_scroll = ui.scroll_area().classes("flex-1 w-full").style(
                    "height: calc(100vh - 120px)"
                )
                with chat_scroll:
                    chat_col = ui.column().classes("w-full gap-2 p-4")

            # Right panel — 40%
            with ui.column().classes("h-full overflow-hidden").style("width:40%"):
                with ui.tabs().classes("w-full shrink-0") as tabs:
                    tab_tx = ui.tab("转写", icon="record_voice_over")
                    tab_q = ui.tab("题目", icon="list_alt")
                    tab_r = ui.tab("报告", icon="assessment")

                with ui.tab_panels(tabs, value=tab_tx).classes(
                    "flex-1 w-full overflow-hidden"
                ) as panels:
                    # Transcript tab
                    with ui.tab_panel(tab_tx).classes("h-full flex flex-col gap-2 p-2"):
                        tx_scroll = ui.scroll_area().classes("flex-1 w-full")
                        with tx_scroll:
                            tx_col = ui.column().classes("w-full gap-1 p-1")
                        with ui.row().classes("w-full gap-2 items-center shrink-0"):
                            src_sel = ui.select(
                                {"candidate": "候选人", "interviewer": "面试官"},
                                value="candidate",
                                label="来源",
                            ).classes("w-28")
                            manual_in = ui.input(placeholder="手动输入转写…").classes(
                                "flex-1"
                            )
                            ui.button(
                                icon="send",
                                on_click=lambda: asyncio.create_task(
                                    _send_manual(manual_in, src_sel, send_queue)
                                ),
                            ).props("flat dense color=primary")

                    # Questions tab
                    with ui.tab_panel(tab_q).classes("h-full p-2"):
                        q_scroll = ui.scroll_area().classes("w-full h-full")
                        with q_scroll:
                            q_col = ui.column().classes("w-full gap-1 p-1")

                    # Report tab
                    with ui.tab_panel(tab_r).classes("h-full p-2"):
                        r_scroll = ui.scroll_area().classes("w-full h-full")
                        with r_scroll:
                            r_col = ui.column().classes("w-full gap-2 p-1")

        # Bottom input row
        with ui.row().classes(
            "w-full items-end px-4 py-2 bg-white border-t gap-2 flex-shrink-0"
        ):
            user_in = ui.textarea(placeholder="输入指令或问题…").props(
                "autogrow rows=1 outlined dense"
            ).classes("flex-1")
            candidate_sel = ui.select(
                options={},
                label="选择候选人",
                clearable=True,
            ).props("dense outlined").classes("w-40").tooltip("从历史候选人中选择")
            ui.upload(
                label="",
                on_upload=lambda e: asyncio.create_task(
                    _handle_upload(e, chat_col, chat_scroll, q_col, state)
                ),
                auto_upload=True,
            ).props("accept=.pdf flat dense").tooltip("上传简历 PDF")
            send_btn = ui.button(icon="send").props("flat dense color=primary")

    # ── 候选人选择器 ──────────────────────────────────────────────────────────────

    async def _load_candidates() -> None:
        """页面加载时拉取历史候选人列表，填充下拉框。"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{_base_url}/api/candidates", params={"limit": 50})
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            logger.debug("load_candidates failed: %s", exc)
            return

        candidates = data.get("candidates", [])
        state["candidates"] = candidates
        opts = {}
        for c in candidates:
            cid = c.get("id", "")
            name = c.get("name") or "—"
            skills = c.get("skills", [])
            preview = "、".join(skills[:3]) if skills else ""
            label = f"{name}  {preview}" if preview else name
            opts[cid] = label
        candidate_sel.set_options(opts)

    async def _on_candidate_select(cid: str | None) -> None:
        """候选人选中后：加载其 profile + 最新题目计划，跳过上传流程。"""
        if not cid:
            return

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{_base_url}/api/resume/profile", params={"candidate_id": cid}
                )
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            _error(chat_col, f"加载候选人信息失败：{exc}")
            await _scroll(chat_scroll)
            return

        profile = data.get("profile", {})
        questions = data.get("questions", [])

        state["candidate_id"] = cid
        state["candidate_name"] = profile.get("name") or "—"
        _refresh_bar(stage_badge, candidate_label, round_label, state)

        skills = ", ".join((profile.get("skills") or [])[:8])
        q_lines = "\n".join(
            f"  {i+1}. {q.get('question', '')}" for i, q in enumerate(questions[:10])
        )
        if len(questions) > 10:
            q_lines += f"\n  …共 {len(questions)} 道"
        reply = (
            f"已选择历史候选人：{profile.get('name', '—')}\n"
            f"技能：{skills or '—'}\n\n"
            + (
                f"面试题目（{len(questions)} 道）：\n{q_lines}"
                if questions
                else "暂无历史题目，可点击「开始面试」重新生成。"
            )
        )
        _bubble(chat_col, reply, sent=False, name="Agent")
        if questions:
            _render_questions(q_col, questions, cid)
        await _scroll(chat_scroll)

    candidate_sel.on(
        "update:model-value",
        lambda e: asyncio.create_task(_on_candidate_select(e.args)),
    )
    asyncio.create_task(_load_candidates())

    # ── Interaction handlers ───────────────────────────────────────────────────

    async def _do_send() -> None:
        text = user_in.value.strip()
        if not text:
            return
        user_in.value = ""

        # @candidate: / @interviewer: → send as manual_input WS message
        lo = text.lower()
        for prefix, source in (("@candidate:", "candidate"), ("@interviewer:", "interviewer")):
            if lo.startswith(prefix):
                clean = text[len(prefix):].strip()
                if clean:
                    label = "候选人" if source == "candidate" else "面试官"
                    _bubble(chat_col, f"[{label}] {clean}", sent=True, name="你")
                    await _scroll(chat_scroll)
                    payload = json.dumps({"type": "manual_input", "source": source, "text": clean})
                    await send_queue.put(payload)
                return

        _bubble(chat_col, text, sent=True, name="你")
        await _scroll(chat_scroll)
        await _agent_loop(text, chat_col, chat_scroll, panels, tab_r, r_col, q_col, state)

    send_btn.on("click", lambda: asyncio.create_task(_do_send()))
    user_in.on(
        "keydown.enter",
        lambda e: asyncio.create_task(_do_send())
        if not (e.args.get("shiftKey") or e.args.get("ctrlKey"))
        else None,
    )

    async def _on_start() -> None:
        cid = state.get("candidate_id")
        if not cid:
            _error(chat_col, "请先上传简历以获取候选人 ID")
            await _scroll(chat_scroll)
            return
        try:
            result = await _ict.start_interview(cid)
            stage = result.get("stage", "interviewing")
            state["stage"] = stage
            _refresh_bar(stage_badge, candidate_label, round_label, state)
            _bubble(chat_col, f"面试已开始！当前阶段：{stage}", sent=False, name="Agent")
        except Exception as exc:
            _error(chat_col, f"开始面试失败：{exc}")
        await _scroll(chat_scroll)

    async def _on_stop() -> None:
        try:
            result = await _ict.stop_interview()
            total = result.get("total_rounds", 0)
            _bubble(chat_col, f"面试已结束，共 {total} 轮对话。正在生成评价报告…", sent=False, name="Agent")
            await _scroll(chat_scroll)
            # Auto-fetch report
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(f"{_base_url}/api/interview/eval")
                r.raise_for_status()
                report_data = r.json().get("report", {})
            _render_report(r_col, report_data)
            panels.set_value(tab_r)
            _bubble(chat_col, "评价报告已生成，请查看「报告」Tab。", sent=False, name="Agent")
        except Exception as exc:
            _error(chat_col, f"结束面试失败：{exc}")
        await _scroll(chat_scroll)

    start_btn.on("click", lambda: asyncio.create_task(_on_start()))
    stop_btn.on("click", lambda: asyncio.create_task(_on_stop()))

    # ── WebSocket background tasks ─────────────────────────────────────────────

    async def _ws_receiver() -> None:
        while True:
            try:
                async with ws_connect(_ws_url) as ws:
                    ws_icon.classes(remove="text-grey-5").classes("text-green-5")
                    ws_icon.props("name=wifi")
                    state["ws_connected"] = True
                    logger.info("WS connected %s", _ws_url)

                    # Sender coroutine
                    async def _sender() -> None:
                        while True:
                            payload = await send_queue.get()
                            await ws.send(payload)

                    sender_task = asyncio.create_task(_sender())
                    try:
                        async for raw in ws:
                            try:
                                msg = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            await recv_queue.put(msg)
                    finally:
                        sender_task.cancel()
            except Exception as exc:
                ws_icon.classes(remove="text-green-5").classes("text-grey-5")
                ws_icon.props("name=wifi_off")
                state["ws_connected"] = False
                logger.debug("WS disconnected: %s — retry in 3s", exc)
                await asyncio.sleep(3)

    asyncio.create_task(_ws_receiver())

    # ── Queue poll timer ───────────────────────────────────────────────────────

    async def _poll() -> None:
        while not recv_queue.empty():
            msg = recv_queue.get_nowait()
            await _dispatch(
                msg, state,
                chat_col, chat_scroll,
                tx_col, tx_scroll,
                q_col, r_col,
                panels, tab_r,
                stage_badge, candidate_label, round_label,
            )

    ui.timer(0.1, _poll)


# ── WS message dispatcher ─────────────────────────────────────────────────────

async def _dispatch(
    msg: dict, state: dict,
    chat_col, chat_scroll,
    tx_col, tx_scroll,
    q_col, r_col,
    panels, tab_r,
    stage_badge, candidate_label, round_label,
) -> None:
    t = msg.get("type", "")

    if t == "transcript":
        source = msg.get("source", "")
        text = msg.get("text", "")
        is_final = msg.get("is_final", False)
        label = "候选人" if source == "candidate" else "面试官"
        cls = "text-xs text-grey-8 border-b pb-1" if is_final else "text-xs text-grey-5 italic"
        with tx_col:
            ui.label(f"[{label}] {text}").classes(cls)
        tx_scroll.scroll_to(percent=1.0)
        # Lightweight inline entry in chat area (final segments only)
        if is_final:
            with chat_col:
                with ui.card().classes(
                    "w-full bg-grey-1 border-l-2 border-grey-4 px-3 py-1"
                ):
                    ui.label(f"[{label}] {text}").classes("text-xs text-grey-7")
            await _scroll(chat_scroll)

    elif t == "suggestion_delta":
        delta = msg.get("delta", "")
        if state["suggestion_card"] is None:
            with chat_col:
                card = ui.card().classes(
                    "w-full bg-amber-50 border-l-4 border-amber-400 p-3"
                )
                with card:
                    ui.label("AI 追问建议").classes("text-xs font-bold text-amber-700")
                    lbl = ui.label("").classes("text-sm mt-1 whitespace-pre-wrap")
            state["suggestion_card"] = card
            state["suggestion_label"] = lbl
            state["suggestion_text"] = ""
            await _scroll(chat_scroll)
        state["suggestion_text"] += delta
        state["suggestion_label"].set_text(state["suggestion_text"])

    elif t == "suggestion_final":
        final_text = msg.get("text", state.get("suggestion_text", ""))
        state["suggestion_text"] = final_text
        card = state["suggestion_card"]
        if card is None:
            with chat_col:
                card = ui.card().classes(
                    "w-full bg-amber-50 border-l-4 border-amber-400 p-3"
                )
                state["suggestion_card"] = card
                with card:
                    ui.label("AI 追问建议").classes("text-xs font-bold text-amber-700")
                    ui.label(final_text).classes("text-sm mt-1 whitespace-pre-wrap")

        captured = final_text

        def _accept() -> None:
            _bubble(chat_col, captured, sent=True, name="面试官")
            asyncio.create_task(_scroll(chat_scroll))

        with card:
            with ui.row().classes("mt-2 gap-2"):
                ui.button("采用", icon="check", on_click=_accept).props(
                    "flat dense color=positive"
                )
                ui.button("忽略", icon="close").props("flat dense color=grey")

        state["suggestion_card"] = None
        state["suggestion_label"] = None
        state["suggestion_text"] = ""
        await _scroll(chat_scroll)

    elif t == "session_snapshot":
        stage = msg.get("stage", state.get("stage", "idle"))
        rounds_count = msg.get("rounds_count", state.get("round_count", 0))
        state["stage"] = stage
        state["round_count"] = rounds_count
        candidate_name = msg.get("candidate_name", "")
        if candidate_name and candidate_name != "—":
            state["candidate_name"] = candidate_name
        _refresh_bar(stage_badge, candidate_label, round_label, state)

        questions = msg.get("question_plan", [])
        if questions:
            _render_questions(q_col, questions, state.get("candidate_id"))

    elif t == "status":
        stage = msg.get("stage", "")
        if stage and stage != state.get("stage"):
            state["stage"] = stage
            _refresh_bar(stage_badge, candidate_label, round_label, state)
        logger.debug("WS status: %s", msg.get("message", ""))

    elif t == "error":
        _error(chat_col, msg.get("message", str(msg)))
        await _scroll(chat_scroll)

    elif t == "heartbeat":
        pass


# ── UI Agent loop ─────────────────────────────────────────────────────────────

async def _agent_loop(
    user_text: str,
    chat_col, chat_scroll,
    panels, tab_r, r_col,
    q_col,
    state: dict,
) -> None:
    if _llm_client is None or _tool_registry is None:
        _error(chat_col, "后端服务尚未初始化，请稍候重试")
        await _scroll(chat_scroll)
        return

    state["agent_history"].append(Message(role="user", content=user_text))

    tool_names = [
        "start_interview", "stop_interview", "get_eval_report",
        "request_suggestion", "regenerate_questions",
    ]
    tools = _tool_registry.get_schemas(tool_names) or None
    messages = [Message(role="system", content=_UI_AGENT_SYSTEM)] + state["agent_history"]

    try:
        resp = await _llm_client.chat(messages=messages, tools=tools)
    except Exception as exc:
        logger.exception("UI Agent LLM call failed")
        _error(chat_col, f"LLM 调用失败：{exc}")
        await _scroll(chat_scroll)
        return

    assistant_msg = Message(role="assistant", content=resp.content or "")

    if resp.tool_calls:
        assistant_msg.tool_calls = resp.tool_calls
        state["agent_history"].append(assistant_msg)

        for tc in resp.tool_calls:
            result_str = await _tool_registry.dispatch(tc.function.name, tc.function.arguments)
            state["agent_history"].append(
                Message(role="tool", content=result_str, tool_call_id=tc.id)
            )

            try:
                result = json.loads(result_str)
            except json.JSONDecodeError:
                result = {"result": result_str}

            reply = _fmt_tool(tc.function.name, result, state)

            if tc.function.name == "stop_interview" and "error" not in result:
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        r = await client.get(f"{_base_url}/api/interview/eval")
                        r.raise_for_status()
                        report_data = r.json().get("report", {})
                    _render_report(r_col, report_data)
                    panels.set_value(tab_r)
                    reply += "\n\n评价报告已生成，请查看「报告」Tab。"
                except Exception as exc:
                    logger.warning("Auto-fetch report failed: %s", exc)

            if tc.function.name == "regenerate_questions" and "error" not in result:
                questions = result.get("questions", [])
                if questions:
                    _render_questions(q_col, questions, state.get("candidate_id"))

            _bubble(chat_col, reply, sent=False, name="Agent")
    else:
        if resp.content:
            state["agent_history"].append(assistant_msg)
            _bubble(chat_col, resp.content, sent=False, name="Agent")

    # Keep history bounded
    if len(state["agent_history"]) > 24:
        state["agent_history"] = state["agent_history"][-24:]

    await _scroll(chat_scroll)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bubble(col, text: str, *, sent: bool, name: str) -> None:
    with col:
        ui.chat_message(text=text, name=name, sent=sent)


def _error(col, text: str) -> None:
    with col:
        with ui.card().classes("w-full bg-red-1 border-l-4 border-red-5 p-2"):
            ui.label(f"错误：{text}").classes("text-sm text-red-8")


async def _scroll(area) -> None:
    area.scroll_to(percent=1.0)


def _refresh_bar(badge, cand_label, rnd_label, state: dict) -> None:
    stage = state.get("stage", "idle")
    color = _STAGE_COLORS.get(stage, "grey")
    badge.set_text(stage)
    badge.props(f"rounded color={color}")
    cand_label.set_text(f"候选人：{state.get('candidate_name', '—')}")
    rnd_label.set_text(f"轮次：{state.get('round_count', 0)}")


def _render_questions(col, questions: list, candidate_id: str | None = None) -> None:
    col.clear()
    q_list = [dict(q) for q in questions if isinstance(q, dict)]

    def _make_toggle(idx: int):
        async def toggle(e) -> None:
            q_list[idx]["is_covered"] = e.value
            if candidate_id:
                try:
                    async with httpx.AsyncClient(timeout=5) as client:
                        await client.put(
                            f"{_base_url}/api/interview/questions",
                            json={"candidate_id": candidate_id, "questions": q_list},
                        )
                except Exception:
                    pass
        return toggle

    with col:
        for i, q in enumerate(q_list):
            text = q.get("question", "")
            dim = q.get("dimension", "")
            covered = q.get("is_covered", False)
            cls = "text-sm text-grey-5 line-through flex-1" if covered else "text-sm flex-1"
            with ui.row().classes("w-full items-start gap-1 py-1"):
                ui.checkbox("", value=covered, on_change=_make_toggle(i)).props("dense")
                ui.label(f"[{dim}] {text}").classes(cls)


def _render_report(col, report: dict) -> None:
    col.clear()
    if not report:
        with col:
            ui.label("暂无报告数据").classes("text-grey-5")
        return
    with col:
        overall = report.get("overall_score", "—")
        ui.label(f"综合得分：{overall}").classes("text-h6 font-bold")
        rec = report.get("recommendation", "")
        if rec:
            ui.label(f"建议：{rec}").classes("text-sm text-grey-7 mt-1")
        for dim in report.get("dimensions", report.get("scores", [])):
            if not isinstance(dim, dict):
                continue
            name = dim.get("dimension", dim.get("name", ""))
            score = dim.get("score", "")
            comment = dim.get("comment", dim.get("feedback", ""))
            with ui.expansion(f"{name} — {score} 分").classes("w-full"):
                ui.label(comment).classes("text-sm text-grey-7")


async def _handle_upload(event: Any, chat_col, chat_scroll, q_col, state: dict) -> None:
    filename = event.name
    content = event.content.read()
    logger.info("PDF upload: %s (%d bytes)", filename, len(content))

    _bubble(chat_col, f"正在解析简历：{filename}…", sent=False, name="Agent")
    await _scroll(chat_scroll)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{_base_url}/api/resume/upload",
                files={"file": (filename, content, "application/pdf")},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.exception("PDF upload failed")
        _error(chat_col, f"简历上传失败：{exc}")
        await _scroll(chat_scroll)
        return

    profile = data.get("profile", {})
    questions = data.get("questions", [])
    cid = data.get("candidate_id", "")

    state["candidate_id"] = cid
    state["candidate_name"] = profile.get("name", "—")

    skills = ", ".join(profile.get("skills", [])[:8])
    q_lines = "\n".join(
        f"  {i+1}. {q.get('question', '')}" for i, q in enumerate(questions[:10])
    )
    if len(questions) > 10:
        q_lines += f"\n  …共 {len(questions)} 道"
    reply = (
        f"简历解析完成！\n"
        f"候选人：{profile.get('name', '—')}\n"
        f"技能：{skills or '—'}\n\n"
        f"面试题目（{len(questions)} 道）：\n{q_lines}"
    )
    _bubble(chat_col, reply, sent=False, name="Agent")
    if questions:
        _render_questions(q_col, questions, cid)
    await _scroll(chat_scroll)


async def _send_manual(manual_in: Any, src_sel: Any, send_queue: Queue[str]) -> None:
    text = manual_in.value.strip()
    if not text:
        return
    manual_in.value = ""
    payload = json.dumps({"type": "manual_input", "source": src_sel.value, "text": text})
    await send_queue.put(payload)


def _fmt_tool(tool_name: str, result: dict, state: dict) -> str:
    if "error" in result:
        return f"操作失败：{result['error']}"
    if tool_name == "start_interview":
        stage = result.get("stage", "interviewing")
        state["stage"] = stage
        return f"面试已开始！当前阶段：{stage}"
    if tool_name == "stop_interview":
        return f"面试已结束，共 {result.get('total_rounds', 0)} 轮对话。"
    if tool_name == "get_eval_report":
        rpt = result.get("report", {})
        score = rpt.get("overall_score", "—")
        rec = rpt.get("recommendation", "")
        return f"评价报告已生成。\n综合得分：{score}\n建议：{rec}"
    if tool_name == "request_suggestion":
        return "追问建议已触发，请稍等 AI 生成建议。"
    if tool_name == "regenerate_questions":
        count = len(result.get("questions", []))
        return f"已重新生成 {count} 道面试题。"
    return f"操作完成：{json.dumps(result, ensure_ascii=False)}"
