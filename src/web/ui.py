"""NiceGUI 单页面界面 — 纯 UI 层，无 Agent 逻辑。

聊天框通过 POST /api/chat 与 MainAgent 通信（SSE 流式），
候选人切换通过 POST /api/candidate/select 更新上下文。
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

logger = logging.getLogger(__name__)

# ── Module-level dependencies (injected by main.py at startup) ────────────────

_base_url: str = "http://127.0.0.1:8000"
_ws_url: str = "ws://127.0.0.1:8000/ws/interview"
_startup_warnings: list[str] = (
    []
)  # S-15: lifespan 启动校验失败的提示，UI 顶部红色横幅显示

_STAGE_COLORS = {
    "idle": "grey",
    "resume_analysis": "blue",
    "interviewing": "green",
    "evaluating": "orange",
    "completed": "purple",
}

_STAGE_LABELS = {
    "idle": "空闲",
    "resume_analysis": "分析中",
    "interviewing": "面试中",
    "evaluating": "评价中",
    "completed": "已完成",
}


def set_dependencies(settings: Any) -> None:
    """注入 UI 所需的 settings；其它依赖通过 app.state / API 访问。"""
    global _base_url, _ws_url
    _base_url = f"http://{settings.HOST}:{settings.PORT}"
    _ws_url = f"ws://{settings.HOST}:{settings.PORT}/ws/interview"


def set_startup_warnings(warnings: list[str]) -> None:
    """由 main.lifespan 注入启动配置校验告警；为空时 UI 不显示横幅。"""
    global _startup_warnings
    _startup_warnings = list(warnings or [])


# ── Page registration ─────────────────────────────────────────────────────────


@ui.page("/")
async def index() -> None:
    """Main dialog page — one instance per browser connection."""

    ui.colors(
        primary="#2563EB",
        secondary="#64748B",
        positive="#059669",
        negative="#DC2626",
        warning="#D97706",
        info="#0284C7",
    )
    ui.add_head_html("""
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --ia-bg: #F1F5F9;          /* slate-100 主背景 */
    --ia-surface: #FFFFFF;
    --ia-border: #E2E8F0;      /* slate-200 */
    --ia-border-soft: #F1F5F9;
    --ia-primary: #2563EB;
    --ia-primary-soft: #EFF6FF;
    --ia-text: #0F172A;        /* slate-900 */
    --ia-text-2: #475569;      /* slate-600 */
    --ia-text-3: #94A3B8;      /* slate-400 */
    --ia-radius: 10px;
    --ia-shadow-sm: 0 1px 2px rgba(15,23,42,0.05);
    --ia-shadow-md: 0 4px 12px rgba(15,23,42,0.08);
  }
  body {
    font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
    background: var(--ia-bg) !important;
    color: var(--ia-text);
  }
  .q-page-container { background: var(--ia-bg) !important; }
  /* 去掉 NiceGUI 默认内容内边距，让布局贴边占满全屏 */
  .nicegui-content { padding: 0; }
  .q-icon, .material-icons, .material-icons-outlined,
  .material-icons-round, .material-icons-sharp {
    font-family: 'Material Icons', 'Material Icons Outlined', 'Material Icons Round', 'Material Icons Sharp' !important;
  }
  /* ── 全局组件打磨 ─────────────────────────────── */
  /* 细滚动条 */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: #94A3B8; }
  ::-webkit-scrollbar-track { background: transparent; }
  /* 按钮统一圆角、去除全大写 */
  .q-btn { border-radius: 8px; text-transform: none; letter-spacing: 0; }
  .q-btn--rounded { border-radius: 999px; }
  /* Tab：紧凑、图标文字同行 */
  .q-tab { min-height: 44px; padding: 0 12px; text-transform: none; }
  .q-tab__label { font-size: 13px; font-weight: 500; }
  .q-tab__icon { font-size: 18px; }
  .q-tab--active { color: var(--ia-primary); }
  .q-tabs { color: var(--ia-text-3); }
  .q-tab__indicator { height: 3px; border-radius: 3px 3px 0 0; }
  /* 输入框 */
  .q-field--outlined .q-field__control { border-radius: 8px; }
  /* 候选人行操作按钮：hover 时才完全显示，降低视觉噪音 */
  .cand-row .cand-del { opacity: 0; transition: opacity .15s; }
  .cand-row:hover .cand-del { opacity: 1; }
  .cand-row .q-checkbox { opacity: .45; transition: opacity .15s; }
  .cand-row:hover .q-checkbox,
  .cand-row .q-checkbox[aria-checked="true"] { opacity: 1; }
  .cand-row { transition: background .12s; }
  .cand-row:hover { background: #F8FAFC; }
  /* 聊天气泡内 markdown 排版：压缩标题字号与段落间距 */
  .chat-md h1, .chat-md h2 { font-size: 1.05rem; font-weight: 700; margin: 0.5em 0 0.3em; line-height: 1.4; }
  .chat-md h3, .chat-md h4 { font-size: 0.95rem; font-weight: 600; margin: 0.4em 0 0.2em; line-height: 1.4; }
  .chat-md p { margin: 0.3em 0; }
  .chat-md ul, .chat-md ol { margin: 0.3em 0; padding-left: 1.4em; }
  .chat-md li { margin: 0.15em 0; }
  .chat-md table { border-collapse: collapse; margin: 0.4em 0; }
  .chat-md th, .chat-md td { border: 1px solid #E5E7EB; padding: 4px 8px; font-size: 12px; }
  .chat-md th { background: #F9FAFB; }
  .chat-md code { background: #F3F4F6; padding: 1px 4px; border-radius: 3px; font-size: 12px; }
  .chat-md pre { background: #F3F4F6; padding: 8px; border-radius: 6px; overflow-x: auto; }
  .chat-md > *:first-child { margin-top: 0; }
  .chat-md > *:last-child { margin-bottom: 0; }
  /* 右侧面板 markdown 排版：标题字号与正文协调 */
  .panel-md h1 { font-size: 1.15rem; font-weight: 700; margin: 0.6em 0 0.3em; }
  .panel-md h2 { font-size: 1.05rem; font-weight: 700; margin: 0.6em 0 0.3em; }
  .panel-md h3 { font-size: 0.95rem; font-weight: 600; margin: 0.5em 0 0.25em; }
  .panel-md h4, .panel-md h5 { font-size: 0.9rem; font-weight: 600; margin: 0.4em 0 0.2em; }
  .panel-md p { margin: 0.3em 0; }
  .panel-md ul, .panel-md ol { margin: 0.3em 0; padding-left: 1.4em; }
  .panel-md li { margin: 0.15em 0; }
  .panel-md hr { margin: 0.8em 0; border-color: #E5E7EB; }
  /* 候选人列表：滚动内容不允许超出面板宽度（Quasar 滚动内容默认按子元素撑开） */
  .candidate-scroll .q-scrollarea__content { width: 100%; }
  /* 工具调用卡片：压低头部高度，弱化存在感 */
  .tool-card .q-item { min-height: 32px; padding: 4px 12px; }
  .tool-card .q-item__label { font-size: 12px; color: var(--ia-text-2); }
  .tool-card .q-expansion-item__content { padding: 4px 12px 8px; }
</style>
""")

    # Per-page state
    state: dict[str, Any] = {
        "candidate_id": None,
        "candidate_name": "—",
        "stage": "idle",
        "round_count": 0,
        "trigger_mode": "auto",
        "suggestion_label": None,
        "suggestion_text": "",
        "suggestion_card": None,
        "candidates": [],
        "candidate_list_col": None,
    }
    recv_queue: Queue[dict] = Queue()
    send_queue: Queue[str] = Queue()

    # ── Layout ────────────────────────────────────────────────────────────────
    ui.query("body").style("margin:0; overflow:hidden;")

    with ui.column().classes("w-full h-screen gap-0"):
        # S-15: 启动配置告警横幅（缺少 LLM_API_KEY 等）
        if _startup_warnings:
            with (
                ui.column()
                .classes("w-full flex-shrink-0")
                .style("background:#FEF2F2; border-bottom:1px solid #FECACA;")
            ):
                for _w in _startup_warnings:
                    with ui.row().classes("w-full items-center px-5 py-2 gap-2"):
                        ui.icon("error").classes("text-red-7").style("font-size:18px;")
                        ui.label(_w).classes("text-sm text-red-9").style(
                            "white-space:normal; word-break:break-word;"
                        )

        # Top status bar
        with (
            ui.row()
            .classes("w-full items-center px-5 gap-3 flex-shrink-0 no-wrap")
            .style(
                "background:white; border-bottom:1px solid var(--ia-border); "
                "height:56px;"
            )
        ):
            # 产品品牌区：渐变圆角 logo + 名称
            with (
                ui.element("div")
                .classes("flex items-center justify-center")
                .style(
                    "width:32px; height:32px; border-radius:9px;"
                    "background:linear-gradient(135deg,#3B82F6 0%,#6366F1 100%);"
                    "box-shadow:0 2px 6px rgba(59,130,246,0.35);"
                )
            ):
                ui.icon("support_agent").style("color:white; font-size:19px;")
            ui.label("面试助手").classes("text-base font-bold").style(
                "color:var(--ia-text); letter-spacing:0.3px;"
            )
            ui.element("div").style(
                "width:1px; height:22px; background:var(--ia-border); margin:0 6px;"
            )
            stage_badge = ui.badge("空闲").props("rounded color=grey").classes(
                "px-3 py-1 text-xs"
            )
            with ui.column().classes("gap-0 leading-none"):
                candidate_label = ui.label("—").classes(
                    "text-sm font-semibold"
                ).style("color:var(--ia-text);")
                round_label = ui.label("轮次：0").classes("text-xs").style(
                    "color:var(--ia-text-3);"
                )
            ui.space()
            # L3-3: 音频状态 badge — 默认隐藏，audio_status WS 消息到达时显示
            audio_badge = (
                ui.badge("音频未启用")
                .props("rounded color=negative outline")
                .tooltip("")
            )
            audio_badge.set_visibility(False)
            state["audio_badge"] = audio_badge
            start_btn = (
                ui.button("开始面试", icon="play_arrow")
                .props("unelevated no-caps color=primary")
                .classes("px-4")
                .style("border-radius:999px; font-weight:600;")
            )
            stop_btn = (
                ui.button("结束面试", icon="stop")
                .props("outline no-caps color=negative")
                .classes("px-4")
                .style("border-radius:999px; font-weight:600;")
            )
            ws_icon = ui.icon("wifi_off").classes("text-base").style(
                "color:var(--ia-text-3);"
            )

        # Main body
        with ui.row().classes("flex-1 w-full overflow-hidden"):
            # Left panel — candidate list (236px)
            with (
                ui.column()
                .classes("h-full flex-shrink-0 overflow-hidden")
                .style(
                    "width:236px; background:white;"
                    "border-right:1px solid var(--ia-border);"
                )
            ):
                with ui.row().classes(
                    "w-full items-center px-3 pt-3 pb-1 gap-1 shrink-0"
                ):
                    ui.label("候选人").classes("text-sm font-bold flex-1").style(
                        "color:var(--ia-text);"
                    )

                    async def _do_upload_left(e: Any) -> None:
                        await _handle_upload(
                            e,
                            chat_col,
                            chat_scroll,
                            q_col,
                            state,
                            on_chat_complete=_sync_candidate_panel,
                        )
                        _refresh_bar(stage_badge, candidate_label, round_label, state)
                        await _sync_candidate_panel()

                    _uploader = (
                        ui.upload(
                            on_upload=lambda e: asyncio.create_task(_do_upload_left(e)),
                            auto_upload=True,
                        )
                        .props("accept=.pdf")
                        .style("display:none")
                    )
                    ui.button("上传简历", icon="upload_file").props(
                        "unelevated dense color=primary no-caps size=sm"
                    ).tooltip("上传 PDF 简历").classes("shrink-0").style(
                        "border-radius:8px;"
                    ).on("click", lambda: _uploader.run_method("pickFiles"))

                def _on_search_change(e: Any) -> None:
                    state["candidate_filter"] = (e.value or "").strip()
                    _render_candidate_list(
                        candidate_list_col,
                        state.get("candidates", []),
                        state,
                        chat_col,
                        chat_scroll,
                        q_col,
                        profile_col,
                        panels,
                        tab_profile,
                        stage_badge,
                        candidate_label,
                        round_label,
                        r_col=r_col,
                        tab_r=tab_r,
                        qs_col=qs_col,
                    )

                with ui.element("div").classes("w-full px-2 pt-1 pb-1"):
                    ui.input(
                        placeholder="搜索姓名 / 职位 / 技能",
                        on_change=_on_search_change,
                    ).props(
                        'dense borderless clearable input-class="text-[13px]"'
                    ).classes("w-full px-3").style(
                        "background:var(--ia-bg); border-radius:8px;"
                    )

                # candidate-scroll：配合全局 CSS 强制滚动内容宽度=容器宽度，
                # 避免内部元素把列表撑宽导致行尾删除按钮溢出到可视区外
                candidate_list_scroll = ui.scroll_area().classes(
                    "flex-1 w-full candidate-scroll"
                )
                with candidate_list_scroll:
                    candidate_list_col = ui.column().classes("w-full gap-0 p-1")
                state["candidate_list_col"] = candidate_list_col

            # Chat area — ~45%
            with (
                ui.column()
                .classes("h-full overflow-hidden")
                .style(
                    "flex:1; min-width:0; background:var(--ia-bg);"
                    "border-right:1px solid var(--ia-border);"
                )
            ):
                chat_scroll = ui.scroll_area().classes("flex-1 w-full h-full")
                with chat_scroll:
                    chat_col = ui.column().classes("w-full gap-1 p-4")
                    _render_welcome(chat_col)

            # Right panel — 38%
            with (
                ui.column()
                .classes("h-full overflow-hidden flex-shrink-0")
                .style("width:38%; background:white;")
            ):
                with (
                    ui.tabs()
                    .props(
                        "dense no-caps inline-label active-color=primary "
                        "indicator-color=primary"
                    )
                    .classes("w-full shrink-0")
                    .style(
                        "background:white;"
                        "border-bottom:1px solid var(--ia-border);"
                    ) as tabs
                ):
                    tab_tx = ui.tab("转写", icon="record_voice_over")
                    tab_q = ui.tab("简报", icon="article")
                    tab_qs = ui.tab("问题", icon="checklist")
                    tab_r = ui.tab("报告", icon="assessment")
                    tab_profile = ui.tab("简历", icon="person")

                with ui.tab_panels(tabs, value=tab_tx).classes(
                    "flex-1 w-full overflow-hidden"
                ) as panels:
                    with ui.tab_panel(tab_tx).classes("h-full p-2"):
                        tx_scroll = ui.scroll_area().classes("w-full h-full")
                        with tx_scroll:
                            tx_col = ui.column().classes("w-full gap-1 p-1")
                            with tx_col:
                                state["tx_placeholder"] = _panel_placeholder(
                                    "record_voice_over",
                                    "面试开始后，双声道语音转写\n将实时显示在这里",
                                )

                    with ui.tab_panel(tab_q).classes("h-full p-2"):
                        q_scroll = ui.scroll_area().classes("w-full h-full")
                        with q_scroll:
                            q_col = ui.column().classes("w-full gap-1 p-1")
                            with q_col:
                                _panel_placeholder(
                                    "article",
                                    "选择候选人并在对话中确认关注点后，\n面试简报将显示在这里",
                                )

                    with ui.tab_panel(tab_qs).classes("h-full p-2"):
                        qs_scroll = ui.scroll_area().classes("w-full h-full")
                        with qs_scroll:
                            qs_col = ui.column().classes("w-full gap-1 p-1")
                            with qs_col:
                                _panel_placeholder(
                                    "checklist",
                                    "简报生成后自动整理结构化问题清单，\n面试中实时追踪覆盖进度",
                                )

                    with ui.tab_panel(tab_r).classes("h-full p-2"):
                        r_scroll = ui.scroll_area().classes("w-full h-full")
                        with r_scroll:
                            r_col = ui.column().classes("w-full gap-2 p-1")
                            with r_col:
                                _panel_placeholder(
                                    "assessment",
                                    "面试结束后，AI 将基于完整对话\n自动生成评价报告",
                                )

                    with ui.tab_panel(tab_profile).classes("h-full p-2"):
                        profile_scroll = ui.scroll_area().classes("w-full h-full")
                        with profile_scroll:
                            profile_col = ui.column().classes("w-full gap-2 p-1")
                            with profile_col:
                                _panel_placeholder(
                                    "person",
                                    "选择左侧候选人后，\n简历详情将显示在这里",
                                )

                with (
                    ui.row()
                    .classes("w-full gap-2 items-center shrink-0 px-3 py-2")
                    .style(
                        "border-top:1px solid var(--ia-border); background:white;"
                    )
                ):
                    mode_btn = (
                        ui.button("⚡ 自动追问")
                        .props("unelevated dense color=positive no-caps size=sm")
                        .style("border-radius:999px; padding:2px 14px;")
                    )
                    with mode_btn:
                        mode_tooltip = ui.tooltip("点击切换为手动模式")
                    ui.space()
                    trigger_btn = (
                        ui.button("触发追问", icon="play_circle")
                        .props("outline dense color=warning no-caps size=sm")
                        .style("border-radius:999px; padding:2px 14px;")
                        .tooltip("手动触发 AI 追问建议")
                    )
                    trigger_btn.disable()

        # Bottom input row：胶囊输入框 + 圆形发送按钮
        with (
            ui.row()
            .classes("w-full items-end px-4 py-3 gap-2 flex-shrink-0")
            .style("background:white; border-top:1px solid var(--ia-border);")
        ):
            user_in = (
                ui.textarea(placeholder="输入指令或问题，Enter 发送…")
                .props('autogrow rows=1 borderless dense input-class="text-[14px]"')
                .classes("flex-1 px-4")
                .style(
                    "background:var(--ia-bg); border-radius:14px;"
                    "border:1px solid transparent; transition:border .15s;"
                )
            )
            send_btn = (
                ui.button(icon="arrow_upward")
                .props("unelevated round color=primary")
                .style("box-shadow:0 2px 8px rgba(37,99,235,0.35);")
            )

    # ── 候选人列表 ─────────────────────────────────────────────────────────────

    async def _load_candidates() -> None:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    f"{_base_url}/api/candidates", params={"limit": 50}
                )
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            logger.debug("load_candidates failed: %s", exc)
            return

        candidates = data.get("candidates", [])
        state["candidates"] = candidates
        _render_candidate_list(
            candidate_list_col,
            candidates,
            state,
            chat_col,
            chat_scroll,
            q_col,
            profile_col,
            panels,
            tab_profile,
            stage_badge,
            candidate_label,
            round_label,
            r_col=r_col,
            tab_r=tab_r,
            qs_col=qs_col,
        )

    async def _sync_candidate_panel() -> None:
        """解析或对话完成后，同步候选人列表与右侧简历/题目面板。"""
        # A-5: 先与服务端会话对齐当前候选人 —— 对话中解析简历/切换候选人后，
        # 前端 state 可能滞后于后端会话
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{_base_url}/api/session/current")
                r.raise_for_status()
                session = r.json().get("session") or {}
            sess_cid = session.get("candidate_id")
            if sess_cid and sess_cid != state.get("candidate_id"):
                state["candidate_id"] = sess_cid
                cname = session.get("candidate_name") or ""
                if cname and cname != "—":
                    state["candidate_name"] = cname
                _refresh_bar(stage_badge, candidate_label, round_label, state)
        except Exception as exc:
            logger.debug("sync session current failed: %s", exc)
        await _load_candidates()
        cid = state.get("candidate_id")
        if not cid:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{_base_url}/api/resume/profile",
                    params={"candidate_id": cid},
                )
                if r.status_code == 404:
                    return
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            logger.debug("sync_candidate_panel failed: %s", exc)
            return
        profile = data.get("profile", {})
        if profile.get("name"):
            state["candidate_name"] = profile["name"]
            _refresh_bar(stage_badge, candidate_label, round_label, state)
        brief = data.get("brief", "")
        resume_markdown = data.get("resume_markdown", "")
        _render_profile_tab(profile_col, profile, brief, resume_markdown)
        _render_brief(q_col, brief)
        # 同步结构化问题清单
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                rq = await client.get(
                    f"{_base_url}/api/interview/questions",
                    params={"candidate_id": cid},
                )
                if rq.status_code == 200:
                    questions = rq.json().get("questions", [])
                    _render_questions(qs_col, questions, cid)
                    if not questions:
                        asyncio.create_task(_retry_questions_later(cid, qs_col))
        except Exception:
            pass

    asyncio.create_task(_load_candidates())

    async def _restore_session_state() -> None:
        """页面加载时从服务端恢复会话状态，避免刷新后 UI 与后端不一致。"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{_base_url}/api/session/current")
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            logger.debug("restore_session_state failed: %s", exc)
            return
        session = data.get("session")
        if not session:
            return
        stage = session.get("stage", "idle")
        state["stage"] = stage
        cid = session.get("candidate_id")
        if cid:
            state["candidate_id"] = cid
        cname = session.get("candidate_name") or ""
        if cname and cname != "—":
            state["candidate_name"] = cname
        state["round_count"] = session.get("rounds_count", 0)
        trigger_mode = session.get("trigger_mode")
        if trigger_mode:
            state["trigger_mode"] = trigger_mode
        _refresh_bar(stage_badge, candidate_label, round_label, state)
        _refresh_trigger_controls()
        _refresh_stage_buttons()
        if cid:
            await _sync_candidate_panel()
        logger.debug("restore_session_state done stage=%s candidate_id=%s", stage, cid)

    asyncio.create_task(_restore_session_state())

    # ── Chat via /api/chat (SSE) ──────────────────────────────────────────────

    async def _do_send() -> None:
        text = user_in.value.strip()
        if not text:
            return
        user_in.value = ""

        _bubble(chat_col, text, sent=True, name="你")
        await _scroll(chat_scroll)

        # Call /api/chat with SSE streaming
        await _chat_stream(
            text, chat_col, chat_scroll, on_complete=_sync_candidate_panel, state=state
        )

    send_btn.on("click", lambda: asyncio.create_task(_do_send()))
    # keydown.enter.exact：只捕获纯 Enter（排除 Shift+Enter、Ctrl+Enter）
    # .prevent：阻止浏览器默认插入换行，避免 textarea 模型更新的竞态
    user_in.on("keydown.enter.exact.prevent", lambda: asyncio.create_task(_do_send()))

    # ── Button handlers ───────────────────────────────────────────────────────

    async def _on_start() -> None:
        cid = state.get("candidate_id")
        if not cid:
            _error(chat_col, "请先选择候选人或上传简历")
            await _scroll(chat_scroll)
            return
        start_btn.disable()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{_base_url}/api/interview/start",
                    json={
                        "candidate_id": cid,
                        "trigger_mode": state.get("trigger_mode", "auto"),
                    },
                )
                r.raise_for_status()
                result = r.json()
            stage = result.get("stage", "interviewing")
            state["stage"] = stage
            _refresh_bar(stage_badge, candidate_label, round_label, state)
            _refresh_trigger_controls()
            _bubble(
                chat_col, f"面试已开始！当前阶段：{stage}", sent=False, name="Agent"
            )
        except Exception as exc:
            _error(chat_col, f"开始面试失败：{exc}")
        finally:
            _refresh_stage_buttons()
        await _scroll(chat_scroll)

    async def _on_stop() -> None:
        stop_btn.disable()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(f"{_base_url}/api/interview/stop")
                r.raise_for_status()
                result = r.json()
            total = result.get("total_rounds", 0)
            state["stage"] = "completed"
            _refresh_bar(stage_badge, candidate_label, round_label, state)
            _refresh_trigger_controls()
            if total == 0:
                _bubble(
                    chat_col,
                    "面试已结束（无对话记录，跳过评价生成）。",
                    sent=False,
                    name="Agent",
                )
            else:
                _bubble(
                    chat_col,
                    f"面试已结束，共 {total} 轮对话。正在生成评价报告…",
                    sent=False,
                    name="Agent",
                )
                await _scroll(chat_scroll)
                async with httpx.AsyncClient(timeout=240) as client:
                    r = await client.get(f"{_base_url}/api/interview/eval")
                    r.raise_for_status()
                    report_data = r.json().get("report", {})
                _render_report(r_col, report_data)
                panels.set_value(tab_r)
                _bubble(
                    chat_col,
                    "评价报告已生成，请查看「报告」Tab。",
                    sent=False,
                    name="Agent",
                )
        except Exception as exc:
            logger.error("_on_stop failed: %s", exc, exc_info=True)
            _error(chat_col, f"结束面试失败：{exc}")
        finally:
            _refresh_stage_buttons()
        await _scroll(chat_scroll)

    start_btn.on("click", lambda: asyncio.create_task(_on_start()))
    stop_btn.on("click", lambda: asyncio.create_task(_on_stop()))

    # ── Stage-dependent button states (A-6) ───────────────────────────────────

    def _refresh_stage_buttons() -> None:
        if state.get("stage") == "interviewing":
            start_btn.disable()
            stop_btn.enable()
        else:
            start_btn.enable()
            stop_btn.disable()

    _refresh_stage_buttons()

    # ── Trigger mode controls ─────────────────────────────────────────────────

    def _refresh_trigger_controls() -> None:
        mode = state.get("trigger_mode", "auto")
        interviewing = state.get("stage") == "interviewing"
        if mode == "auto":
            mode_btn.set_text("⚡ 自动追问")
            mode_btn.props("color=positive")
            mode_tooltip.set_text("点击切换为手动模式")
            trigger_btn.disable()
        else:
            mode_btn.set_text("✋ 手动追问")
            mode_btn.props("color=warning")
            mode_tooltip.set_text("点击切换为自动模式")
            if interviewing:
                trigger_btn.enable()
            else:
                trigger_btn.disable()

    async def _on_toggle_mode() -> None:
        current = state.get("trigger_mode", "auto")
        new_mode = "manual" if current == "auto" else "auto"
        state["trigger_mode"] = new_mode
        _refresh_trigger_controls()
        await send_queue.put(json.dumps({"type": "set_trigger_mode", "mode": new_mode}))

    async def _on_trigger_suggestion() -> None:
        await send_queue.put(json.dumps({"type": "request_suggestion"}))

    mode_btn.on("click", lambda: asyncio.create_task(_on_toggle_mode()))
    trigger_btn.on("click", lambda: asyncio.create_task(_on_trigger_suggestion()))

    # ── WebSocket background tasks ─────────────────────────────────────────────

    async def _ws_receiver() -> None:
        while True:
            try:
                async with ws_connect(_ws_url) as ws:
                    ws_icon.classes(remove="text-grey-5").classes("text-green-5")
                    ws_icon.props("name=wifi")
                    state["ws_connected"] = True

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
                msg,
                state,
                chat_col,
                chat_scroll,
                tx_col,
                tx_scroll,
                q_col,
                qs_col,
                r_col,
                panels,
                tab_r,
                stage_badge,
                candidate_label,
                round_label,
            )
            _refresh_trigger_controls()
            _refresh_stage_buttons()

    ui.timer(0.1, _poll)


# ── WS message dispatcher ─────────────────────────────────────────────────────


async def _dispatch(
    msg: dict,
    state: dict,
    chat_col,
    chat_scroll,
    tx_col,
    tx_scroll,
    q_col,
    qs_col,
    r_col,
    panels,
    tab_r,
    stage_badge,
    candidate_label,
    round_label,
) -> None:
    t = msg.get("type", "")

    if t == "transcript":
        # 首条转写到达时移除空状态占位
        ph = state.pop("tx_placeholder", None)
        if ph is not None:
            try:
                ph.delete()
            except Exception:
                pass
        source = msg.get("source", "")
        text = msg.get("text", "")
        is_final = msg.get("is_final", False)
        label = "候选人" if source == "candidate" else "面试官"
        border_color = "#3B82F6" if source == "candidate" else "#F97316"
        partial_key = f"partial_label_{source}"
        if is_final:
            state.pop(partial_key, None)
            with tx_col:
                with (
                    ui.column()
                    .classes("w-full gap-0 py-1")
                    .style(f"border-left:3px solid {border_color}; padding-left:8px;")
                ):
                    ui.label(label).classes("text-xs font-semibold").style(
                        f"color:{border_color};"
                    )
                    ui.label(text).classes("text-sm text-grey-9")
        else:
            existing = state.get(partial_key)
            if existing is not None:
                existing.set_text(f"{label}：{text}")
            else:
                with tx_col:
                    lbl = ui.label(f"{label}：{text}").classes(
                        "text-xs text-grey-4 italic pl-3"
                    )
                state[partial_key] = lbl
        tx_scroll.scroll_to(percent=1.0)
        if is_final:
            with chat_col:
                with ui.row().classes("w-full justify-center py-1"):
                    ui.label(f"{label}：{text}").style(
                        "background:#F3F4F6; color:#6B7280; padding:3px 12px;"
                        "border-radius:999px; font-size:11px; max-width:80%;"
                        "white-space:pre-wrap; text-align:center;"
                    )
            await _scroll(chat_scroll)

    elif t == "suggestion_delta":
        # L4-8: 用户切到 manual 时丢弃残留 delta（后端虽然取消了，但有时间窗）
        if state.get("trigger_mode") == "manual":
            return
        delta = msg.get("delta", "")
        if state["suggestion_card"] is None:
            with chat_col:
                card, lbl = _suggestion_card("")
            state["suggestion_card"] = card
            state["suggestion_label"] = lbl
            state["suggestion_text"] = ""
            await _scroll(chat_scroll)
        state["suggestion_text"] += delta
        state["suggestion_label"].set_text(state["suggestion_text"])

    elif t == "suggestion_final":
        if msg.get("skipped"):
            state["suggestion_card"] = None
            state["suggestion_label"] = None
            state["suggestion_text"] = ""
            return
        final_text = _clean_suggestion_text(
            msg.get("text", state.get("suggestion_text", ""))
        )
        card = state["suggestion_card"]
        if card is None:
            with chat_col:
                card, _ = _suggestion_card(final_text)
        elif state.get("suggestion_label") is not None:
            # 用清洗后的最终文本替换流式累积内容
            state["suggestion_label"].set_text(final_text)

        state["suggestion_card"] = None
        state["suggestion_label"] = None
        state["suggestion_text"] = ""
        await _scroll(chat_scroll)

    elif t == "session_snapshot":
        stage = msg.get("stage", state.get("stage", "idle"))
        rounds_count = msg.get("rounds_count", state.get("round_count", 0))
        state["stage"] = stage
        state["round_count"] = rounds_count
        trigger_mode = msg.get("trigger_mode")
        if trigger_mode:
            state["trigger_mode"] = trigger_mode
        candidate_name = msg.get("candidate_name", "")
        if candidate_name and candidate_name != "—":
            state["candidate_name"] = candidate_name
        _refresh_bar(stage_badge, candidate_label, round_label, state)

        brief = msg.get("brief", "")
        if brief:
            _render_brief(q_col, brief)

        # 每轮结束后自动触发覆盖检查
        cid = state.get("candidate_id")
        prev_rounds = state.get("round_count_prev", 0)
        if cid and rounds_count > prev_rounds and rounds_count > 0:
            state["round_count_prev"] = rounds_count
            asyncio.create_task(_check_question_coverage(cid, rounds_count, qs_col))

    elif t == "status":
        stage = msg.get("stage", "")
        if stage and stage != state.get("stage"):
            state["stage"] = stage
            _refresh_bar(stage_badge, candidate_label, round_label, state)

    elif t == "error":
        _error(chat_col, msg.get("message", str(msg)))
        await _scroll(chat_scroll)

    elif t == "audio_status":
        # L3-3: 音频启动状态 — 失败时显示红色 badge + tooltip 说明原因
        badge = state.get("audio_badge")
        if badge is None:
            return
        ok = bool(msg.get("ok"))
        if ok:
            badge.set_visibility(False)
        else:
            tip = msg.get("message", "音频未启用，仅支持手动输入")
            badge.set_text("音频未启用")
            badge.tooltip(tip)
            badge.set_visibility(True)
            _error(chat_col, tip)
            await _scroll(chat_scroll)

    elif t == "heartbeat":
        pass


# ── Helpers ───────────────────────────────────────────────────────────────────


def _clean_suggestion_text(text: str) -> str:
    """清洗 AI 追问建议文本：去除首尾空白和包裹引号。"""
    cleaned = text.strip()
    for opening, closing in (('"', '"'), ("“", "”"), ("'", "'"), ("‘", "’"), ("「", "」")):
        if len(cleaned) >= 2 and cleaned.startswith(opening) and cleaned.endswith(closing):
            cleaned = cleaned[1:-1].strip()
    return cleaned


def _panel_placeholder(icon: str, text: str):
    """右侧面板空状态占位（面板重渲染时随 col.clear() 一起移除）。"""
    with ui.column().classes("w-full items-center gap-3 py-12") as ph:
        with (
            ui.element("div")
            .classes("flex items-center justify-center")
            .style(
                "width:48px; height:48px; border-radius:14px;"
                "background:var(--ia-bg);"
            )
        ):
            ui.icon(icon).style("color:var(--ia-text-3); font-size:24px;")
        ui.label(text).classes("text-[13px] text-center whitespace-pre-line").style(
            "color:var(--ia-text-3); line-height:1.7;"
        )
    return ph


_WELCOME_STEPS = (
    ("upload_file", "上传简历", "自动解析 PDF，建立候选人档案"),
    ("article", "生成简报", "结合岗位要求生成面试简报与问题清单"),
    ("graphic_eq", "实时辅助", "面试中实时转写并给出 AI 追问建议"),
    ("assessment", "评价报告", "结束后生成结构化报告，支持多人对比"),
)


def _render_welcome(col) -> None:
    """首次进入时的聊天区欢迎引导卡片。"""
    with col:
        with ui.column().classes("w-full items-center gap-1 pt-10 pb-2"):
            with (
                ui.element("div")
                .classes("flex items-center justify-center")
                .style(
                    "width:52px; height:52px; border-radius:16px;"
                    "background:linear-gradient(135deg,#3B82F6 0%,#6366F1 100%);"
                    "box-shadow:0 6px 18px rgba(59,130,246,0.35);"
                )
            ):
                ui.icon("support_agent").style("color:white; font-size:30px;")
            ui.label("欢迎使用面试助手").classes("text-lg font-bold mt-2").style(
                "color:var(--ia-text);"
            )
            ui.label("你的 AI 面试搭档，覆盖从简历到评价报告的完整流程").classes(
                "text-sm"
            ).style("color:var(--ia-text-2);")
        with ui.grid(columns=2).classes("w-full gap-3 px-6 pt-3 pb-4").style(
            "max-width:640px; margin:0 auto;"
        ):
            for icon, title, desc in _WELCOME_STEPS:
                with (
                    ui.row()
                    .classes("items-start gap-3 p-3 no-wrap")
                    .style(
                        "background:white; border:1px solid var(--ia-border);"
                        "border-radius:12px; box-shadow:var(--ia-shadow-sm);"
                    )
                ):
                    with (
                        ui.element("div")
                        .classes("flex items-center justify-center shrink-0")
                        .style(
                            "width:34px; height:34px; border-radius:9px;"
                            "background:var(--ia-primary-soft);"
                        )
                    ):
                        ui.icon(icon).style(
                            "color:var(--ia-primary); font-size:19px;"
                        )
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(title).classes("text-[13px] font-semibold").style(
                            "color:var(--ia-text);"
                        )
                        ui.label(desc).classes("text-xs").style(
                            "color:var(--ia-text-3); line-height:1.5;"
                        )
        ui.label("从左上角上传一份简历开始，或直接在下方输入框和我对话").classes(
            "text-xs w-full text-center pb-4"
        ).style("color:var(--ia-text-3);")


def _bubble(col, text: str, *, sent: bool, name: str) -> None:
    with col:
        if sent:
            with ui.row().classes("w-full justify-end py-1 px-2"):
                with ui.column().classes("items-end gap-1").style("max-width:68%"):
                    ui.label(name).classes("text-xs px-1").style(
                        "color:var(--ia-text-3);"
                    )
                    ui.label(text).style(
                        "background:linear-gradient(135deg,#2563EB 0%,#3B82F6 100%);"
                        "color:white; padding:10px 14px;"
                        "border-radius:16px 16px 4px 16px; font-size:13px;"
                        "white-space:pre-wrap; line-height:1.6; word-break:break-word;"
                        "box-shadow:0 2px 8px rgba(37,99,235,0.25);"
                    )
        else:
            with ui.row().classes("w-full justify-start py-1 px-2"):
                with ui.column().classes("items-start gap-1").style("max-width:68%"):
                    ui.label(name).classes("text-xs px-1").style(
                        "color:var(--ia-text-3);"
                    )
                    # Agent 回复用 markdown 渲染（加粗/标题/表格等）
                    ui.markdown(text, extras=["tables", "fenced-code-blocks"]).classes(
                        "chat-md"
                    ).style(
                        "background:white; color:var(--ia-text); padding:10px 14px;"
                        "border-radius:16px 16px 16px 4px; font-size:13px;"
                        "line-height:1.6; word-break:break-word;"
                        "border:1px solid var(--ia-border);"
                        "box-shadow:var(--ia-shadow-sm);"
                    )


def _suggestion_card(text: str):
    """AI 追问建议卡片；返回 (card, text_label) 供流式更新。"""
    card = (
        ui.card()
        .classes("w-full p-3 gap-1")
        .style(
            "background:#FFFBEB; border:1px solid #FDE68A; border-radius:12px;"
            "box-shadow:var(--ia-shadow-sm);"
        )
    )
    with card:
        with ui.row().classes("items-center gap-1"):
            ui.icon("tips_and_updates").style("color:#D97706; font-size:16px;")
            ui.label("AI 追问建议").classes("text-xs font-bold").style(
                "color:#B45309;"
            )
        lbl = ui.label(text).classes("text-sm whitespace-pre-wrap").style(
            "color:var(--ia-text); line-height:1.6;"
        )
    return card, lbl


def _error(col, text: str) -> None:
    with col:
        with ui.card().classes("w-full bg-red-1 border-l-4 border-red-5 p-2"):
            ui.label(f"错误：{text}").classes("text-sm text-red-8")


async def _scroll(area) -> None:
    area.scroll_to(percent=1.0)


async def _check_question_coverage(
    candidate_id: str, round_number: int, qs_col
) -> None:
    """调用后端 LLM 检查最新一轮对话覆盖了哪些问题，更新 qs_col。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            lr = await client.get(f"{_base_url}/api/interview/last-round")
            if lr.status_code != 200:
                return
            round_text = lr.json().get("round_text", "")
            if not round_text.strip():
                return
            r = await client.post(
                f"{_base_url}/api/interview/questions/check-coverage",
                json={"candidate_id": candidate_id, "round_text": round_text},
            )
            if r.status_code == 200:
                questions = r.json().get("questions", [])
                _render_questions(qs_col, questions, candidate_id)
    except Exception:
        pass


async def _retry_questions_later(cid: str, qs_col) -> None:
    for attempt in range(3):
        await asyncio.sleep(2 ** attempt)
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                rq = await client.get(
                    f"{_base_url}/api/interview/questions",
                    params={"candidate_id": cid},
                )
                if rq.status_code == 200:
                    questions = rq.json().get("questions", [])
                    if questions:
                        _render_questions(qs_col, questions, cid)
                        return
        except Exception:
            pass


def _refresh_bar(badge, cand_label, rnd_label, state: dict) -> None:
    stage = state.get("stage", "idle")
    color = _STAGE_COLORS.get(stage, "grey")
    label = _STAGE_LABELS.get(stage, stage)
    badge.set_text(label)
    badge.props(f"rounded color={color}")
    cand_label.set_text(state.get("candidate_name", "—"))
    rnd_label.set_text(f"轮次：{state.get('round_count', 0)}")


def _render_brief(col, brief_text: str) -> None:
    col.clear()
    with col:
        if brief_text:
            ui.markdown(brief_text).classes("w-full text-sm panel-md")
        else:
            _panel_placeholder(
                "article",
                "选择候选人并在对话中确认关注点后，\n面试简报将显示在这里",
            )


def _render_questions(col, questions: list, candidate_id: str) -> None:
    col.clear()
    with col:
        if not questions:
            _panel_placeholder(
                "checklist", "暂无问题清单，\n生成面试简报后自动创建"
            )
            return

        covered = sum(1 for q in questions if q.get("covered"))
        total = len(questions)
        bar_color = (
            "#059669" if covered == total else "#D97706" if covered > 0 else "#CBD5E1"
        )
        with ui.column().classes("w-full gap-1 mb-2"):
            with ui.row().classes("w-full items-center no-wrap"):
                ui.label("覆盖进度").classes("text-[13px] font-bold").style(
                    "color:var(--ia-text);"
                )
                ui.space()
                ui.label(f"{covered} / {total}").classes(
                    "text-xs font-semibold"
                ).style(f"color:{bar_color};")
            with (
                ui.element("div")
                .classes("w-full")
                .style(
                    "height:6px; border-radius:3px; background:var(--ia-bg);"
                    "overflow:hidden;"
                )
            ):
                pct = int(covered / total * 100) if total else 0
                ui.element("div").style(
                    f"height:100%; width:{pct}%; background:{bar_color};"
                    "border-radius:3px; transition:width .3s;"
                )

        for q in questions:
            qid = q.get("id", "")
            is_covered = bool(q.get("covered"))
            covered_by = q.get("covered_by", "")
            tag = " ✓" if is_covered else ""
            label_color = "text-green-8" if is_covered else "text-grey-9"
            badge = (
                f" [{'自动' if covered_by == 'auto' else '手动'}]" if is_covered else ""
            )

            with (
                ui.row()
                .classes("w-full items-start gap-2 py-1")
                .style("border-bottom:1px solid var(--ia-border-soft);")
            ):
                async def _toggle(e, _qid=qid, _cid=candidate_id) -> None:
                    try:
                        async with httpx.AsyncClient(timeout=5) as client:
                            await client.patch(
                                f"{_base_url}/api/interview/questions/{_qid}",
                                params={"candidate_id": _cid},
                                json={"covered": e.value},
                            )
                    except Exception:
                        pass

                ui.checkbox(value=is_covered, on_change=_toggle).props("dense size=sm")

                with ui.column().classes("flex-1 gap-0 min-w-0"):
                    ui.label(q.get("question", "") + tag).classes(
                        f"text-sm {label_color} {'line-through' if is_covered else ''}"
                    )
                    focus_text = q.get("focus", "")
                    if focus_text:
                        ui.label(f"考察：{focus_text}{badge}").classes(
                            "text-xs text-grey-5"
                        )


_RECOMMENDATION_LABELS: dict[str, tuple[str, str]] = {
    "strong_hire": ("强烈建议录用", "green"),
    "hire": ("建议录用", "positive"),
    "weak_hire": ("倾向录用", "light-green"),
    "no_hire": ("不建议录用", "negative"),
    "weak_no_hire": ("倾向不录用", "orange"),
    "strong_no_hire": ("强烈不建议录用", "negative"),
}


def _recommendation_display(rec: str) -> tuple[str, str]:
    """将 recommendation 英文枚举映射为中文文案 + 徽章颜色。"""
    return _RECOMMENDATION_LABELS.get(rec.strip().lower(), (rec, "grey"))


def _render_report(col, report: dict) -> None:
    col.clear()
    if not report:
        with col:
            _panel_placeholder("assessment", "暂无报告数据")
        return
    with col:
        overall = report.get("overall_score", "—")
        interview_id = report.get("interview_id", "")
        with (
            ui.row()
            .classes("w-full items-center gap-3 p-3 no-wrap")
            .style(
                "background:white; border:1px solid var(--ia-border);"
                "border-radius:12px; box-shadow:var(--ia-shadow-sm);"
            )
        ):
            with ui.column().classes("gap-0"):
                ui.label("综合得分").classes("text-xs").style(
                    "color:var(--ia-text-3);"
                )
                ui.label(str(overall)).classes("text-2xl font-bold").style(
                    "color:var(--ia-primary); line-height:1.2;"
                )
            rec = report.get("recommendation", "")
            if rec:
                label, badge_color = _recommendation_display(rec)
                ui.badge(label).props(f"rounded color={badge_color}").classes(
                    "text-sm px-3 py-1"
                )
            ui.space()
            if interview_id:
                ui.button(
                    "导出 PDF",
                    on_click=lambda: ui.navigate.to(
                        f"/api/interview/{interview_id}/report/export",
                        new_tab=True,
                    ),
                ).props("icon=download outline dense no-caps size=sm").style(
                    "border-radius:8px;"
                )
        for dim in report.get("dimensions", report.get("scores", [])):
            if not isinstance(dim, dict):
                continue
            name = dim.get("dimension", dim.get("name", ""))
            score = dim.get("score", "")
            comment = dim.get("comment", dim.get("feedback", ""))
            with (
                ui.expansion(f"{name} — {score} 分")
                .props("dense")
                .classes("w-full")
                .style(
                    "background:white; border:1px solid var(--ia-border);"
                    "border-radius:10px;"
                )
            ):
                ui.label(comment).classes("text-sm").style(
                    "color:var(--ia-text-2); line-height:1.7;"
                )


async def _handle_upload(
    event: Any,
    chat_col,
    chat_scroll,
    q_col,
    state: dict,
    on_chat_complete=None,
) -> None:
    filename = event.file.name
    content = await event.file.read()
    logger.info("PDF upload: %s (%d bytes)", filename, len(content))

    _bubble(chat_col, f"正在上传简历：{filename}…", sent=False, name="Agent")
    await _scroll(chat_scroll)

    async def _do_upload_request() -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                r = await client.post(
                    f"{_base_url}/api/resume/upload",
                    files={"file": (filename, content, "application/pdf")},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.exception("PDF upload failed")
            _error(chat_col, f"简历上传失败：{exc}")
            await _scroll(chat_scroll)
            return None

    data = await _do_upload_request()
    if data is None:
        return

    file_path = data.get("file_path", "")
    safe_stem = data.get("safe_stem", "")
    cid = data.get("candidate_id", "")
    state["candidate_id"] = cid
    if safe_stem:
        state["candidate_name"] = safe_stem

    # 展示系统通知气泡，附带「解析简历」确认按钮
    with chat_col:
        with ui.card().classes("w-full bg-blue-50 q-pa-sm"):
            ui.label(f"简历「{filename}」已保存。").classes("text-sm")
            ui.label("点击「解析简历」提取候选人信息并创建档案。").classes(
                "text-xs text-grey-6"
            )
            parse_btn = ui.button(
                "解析简历",
                on_click=lambda: asyncio.ensure_future(
                    _trigger_parse(
                        file_path,
                        safe_stem,
                        parse_btn,
                        chat_col,
                        chat_scroll,
                        on_complete=on_chat_complete,
                        state=state,
                    )
                ),
            ).classes("q-mt-xs")
    await _scroll(chat_scroll)


async def _trigger_parse(
    file_path: str,
    safe_stem: str,
    btn,
    chat_col,
    chat_scroll,
    on_complete=None,
    state: dict | None = None,
) -> None:
    """用户点击「解析简历」按钮后触发的解析请求。"""
    btn.disable()
    md_path = f"resumes/{safe_stem}.md"
    parse_msg = (
        f"简历 {file_path} 已就绪，请解析为 Markdown 并保存为 {md_path}，"
        f"解析完成后提取候选人基本信息（姓名、邮箱、电话、技能、工作年限、职位等）"
    )
    await _chat_stream(parse_msg, chat_col, chat_scroll, on_complete=on_complete, state=state)


async def _chat_stream(
    text: str, chat_col, chat_scroll, on_complete=None, state: dict | None = None
) -> None:
    """调用 /api/chat SSE 接口，流式展示回复。

    SSE 事件类型：
    - {"type": "delta", "delta": "..."}  → 追加到回复气泡
    - {"type": "tool_call", "tool_call_id": "...", "name": "...", "args": "..."}  → 渲染"进行中"工具调用卡片
    - {"type": "tool_result", "tool_call_id": "...", "name": "...",
       "result_summary": "...", "success": ...}  → 按 tool_call_id 原地更新对应卡片
    - {"type": "duplicate_candidate", ...}  → 三选一去重弹窗
    """
    reply_text = ""
    reply_label = None
    tool_cards: dict[str, dict] = {}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=300, write=30, pool=10)
        ) as client:
            async with client.stream(
                "POST",
                f"{_base_url}/api/chat",
                json={"message": text},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    chunk_type = chunk.get("type", "")

                    if chunk_type == "tool_call":
                        tcid = chunk.get("tool_call_id", "")
                        handle = _render_tool_call_card(
                            chat_col, chunk.get("name", ""), chunk.get("args", "")
                        )
                        if tcid:
                            tool_cards[tcid] = handle
                        await _scroll(chat_scroll)

                    elif chunk_type == "tool_result":
                        tcid = chunk.get("tool_call_id", "")
                        handle = tool_cards.get(tcid)
                        if handle is not None:
                            _update_tool_call_card(
                                handle,
                                chunk.get("success", True),
                                chunk.get("result_summary", ""),
                            )
                        else:
                            # 容错：未匹配到对应 tool_call（理论上不应发生），新建一张已完成卡片
                            _render_tool_call_card(chat_col, chunk.get("name", ""), "")
                        await _scroll(chat_scroll)

                    elif chunk_type == "duplicate_candidate":
                        existing_name = chunk.get(
                            "existing_candidate_name", chunk.get("new_name", "")
                        )
                        action = await _confirm_dedup_dialog(
                            existing_name, parent=chat_col
                        )
                        resolved = None
                        try:
                            async with httpx.AsyncClient(timeout=60) as dedup_client:
                                r = await dedup_client.post(
                                    f"{_base_url}/api/resume/resolve-duplicate",
                                    json={
                                        "pending_id": chunk.get("pending_id", ""),
                                        "action": action,
                                    },
                                )
                                r.raise_for_status()
                                resolved = r.json()
                        except Exception as exc:
                            logger.exception("resolve-duplicate failed")
                            _error(chat_col, f"去重处理失败：{exc}")
                        if resolved and resolved.get("action") == "cancel":
                            _bubble(
                                chat_col,
                                "已取消本次上传，未创建候选人档案。",
                                sent=False,
                                name="Agent",
                            )
                        elif resolved:
                            if state is not None:
                                state["candidate_id"] = resolved.get(
                                    "candidate_id", state.get("candidate_id")
                                )
                                if resolved.get("candidate_name"):
                                    state["candidate_name"] = resolved["candidate_name"]
                            _bubble(
                                chat_col, "候选人档案已保存。", sent=False, name="Agent"
                            )
                        await _scroll(chat_scroll)

                    elif chunk_type == "delta":
                        delta = chunk.get("delta", "")
                        if not delta:
                            continue
                        reply_text += delta
                        if reply_label is None:
                            with chat_col:
                                with ui.row().classes("w-full justify-start py-1 px-2"):
                                    with (
                                        ui.column()
                                        .classes("items-start gap-1")
                                        .style("max-width:68%")
                                    ):
                                        ui.label("Agent").classes(
                                            "text-xs px-1"
                                        ).style("color:var(--ia-text-3);")
                                        reply_label = (
                                            ui.markdown(
                                                reply_text,
                                                extras=[
                                                    "tables",
                                                    "fenced-code-blocks",
                                                ],
                                            )
                                            .classes("chat-md")
                                            .style(
                                                "background:white; color:var(--ia-text);"
                                                "padding:10px 14px;"
                                                "border-radius:16px 16px 16px 4px;"
                                                "font-size:13px;"
                                                "line-height:1.6; word-break:break-word;"
                                                "border:1px solid var(--ia-border);"
                                                "box-shadow:var(--ia-shadow-sm);"
                                            )
                                        )
                        else:
                            reply_label.set_content(reply_text)
                        await _scroll(chat_scroll)

    except Exception as exc:
        logger.exception("chat_stream_inner failed")
        _error(chat_col, f"AI 解析失败：{exc}")
    finally:
        if on_complete is not None:
            try:
                await on_complete()
            except Exception:
                logger.exception("chat_stream on_complete failed")
    await _scroll(chat_scroll)


def _render_tool_call_card(col, tool_name: str, args_str: str) -> dict:
    """渲染"进行中"工具调用折叠卡片，返回句柄字典供 tool_result 原地更新。"""
    args_summary = _tool_args_summary(tool_name, args_str)
    with col:
        with ui.row().classes("w-full justify-center py-1"):
            exp = (
                ui.expansion(f"⏳ {tool_name}")
                .props("dense header-class=text-caption")
                .classes("w-full tool-card")
                .style(
                    "max-width:85%; background:white;"
                    "border:1px solid var(--ia-border); border-radius:10px;"
                )
            )
            with exp:
                status = ui.label("执行中…").classes("text-xs text-grey-6")
                ui.label(args_summary or "(无参数摘要)").classes(
                    "text-xs text-grey-5 whitespace-pre-wrap"
                )
    return {"exp": exp, "status": status, "name": tool_name}


def _update_tool_call_card(handle: dict, success: bool, result_summary: str) -> None:
    """收到匹配的 tool_result 事件后，原地更新卡片标题和状态文字。"""
    icon = "✅" if success else "❌"
    handle["exp"].set_text(f"{icon} {handle['name']}")
    handle["status"].set_text(result_summary or ("完成" if success else "失败"))


def _tool_args_summary(tool_name: str, args_str: str) -> str:
    """从工具调用参数中提取简短摘要文字。"""
    try:
        args = json.loads(args_str) if args_str else {}
    except Exception:
        return args_str[:40] if args_str else ""

    if tool_name == "dispatch_to_agent":
        agent = args.get("agent", "")
        task = args.get("task", "")
        task_short = task[:40] + "…" if len(task) > 40 else task
        return f"agent={agent}  {task_short}" if agent else task_short

    if tool_name == "manage_user_memory":
        action = args.get("action", "")
        key = args.get("key", "")
        return f"{action}  {key}" if key else action

    # 通用：显示前两个 key=val
    parts = [f"{k}={str(v)[:20]}" for k, v in list(args.items())[:2]]
    return "  ".join(parts)


async def _confirm_dedup_dialog(existing_name: str, *, parent) -> str:
    """显示三选一去重弹窗，返回 'overwrite' | 'keep_both' | 'cancel'。"""
    done: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    # SSE 流回调中创建 UI 必须进入父容器 slot，否则会触发 NiceGUI slot stack 为空错误。
    with parent:
        with ui.dialog() as dialog, ui.card().classes("p-4 gap-3"):
            ui.label(f"候选人「{existing_name}」已存在").classes("text-base font-semibold")
            ui.label("解析出的姓名与已有候选人重名，请选择处理方式：").classes(
                "text-sm text-grey-7"
            )

            def _choose(action: str) -> None:
                if not done.done():
                    done.set_result(action)
                dialog.close()

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("取消本次上传", on_click=lambda: _choose("cancel")).props(
                    "flat dense"
                )
                ui.button(
                    "保留两份独立档案", on_click=lambda: _choose("keep_both")
                ).props("outline dense")
                ui.button("覆盖已有档案", on_click=lambda: _choose("overwrite")).props(
                    "unelevated dense color=negative"
                )

        dialog.open()
    return await done


def _render_candidate_list(
    col,
    candidates: list,
    state: dict,
    chat_col,
    chat_scroll,
    q_col,
    profile_col,
    panels,
    tab_profile,
    stage_badge,
    candidate_label,
    round_label,
    r_col=None,
    tab_r=None,
    qs_col=None,
) -> None:
    col.clear()
    if not candidates:
        with col:
            with (
                ui.column()
                .classes("w-full items-center gap-2 p-4")
                .style(
                    "border:1.5px dashed #BFDBFE; border-radius:12px; margin:8px;"
                    "background:var(--ia-primary-soft);"
                )
            ):
                ui.icon("upload_file").classes("text-3xl").style(
                    "color:#93C5FD;"
                )
                ui.label("暂无候选人").classes(
                    "text-sm font-semibold text-center"
                ).style("color:var(--ia-text-2);")
                ui.label("点击上方「上传简历」\n添加候选人").classes(
                    "text-xs text-center whitespace-pre-line"
                ).style("color:var(--ia-text-3);")
        return

    # B-7: 按关键字过滤（姓名/职位/技能）
    keyword = (state.get("candidate_filter") or "").lower()
    if keyword:
        candidates = [
            c
            for c in candidates
            if keyword in (c.get("name") or "").lower()
            or keyword in (c.get("current_position") or "").lower()
            or any(keyword in s.lower() for s in (c.get("skills") or []))
        ]
        if not candidates:
            with col:
                ui.label("无匹配候选人").classes(
                    "text-sm text-grey-5 text-center w-full py-4"
                )
            return

    with col:
        # ── compare toolbar ──────────────────────────────────────────────────
        if "selected_for_compare" not in state:
            state["selected_for_compare"] = set()

        compare_bar = ui.row().classes(
            "w-full items-center gap-1 px-1 py-1 flex-wrap"
        )

        async def _do_compare() -> None:
            ids = list(state["selected_for_compare"])
            if len(ids) < 2:
                with col:
                    ui.notify("请至少选择 2 名候选人", type="warning")
                return
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.get(
                        f"{_base_url}/api/candidates/compare",
                        params={"ids": ",".join(ids)},
                    )
                    r.raise_for_status()
                    data = r.json()
            except Exception as exc:
                with col:
                    ui.notify(f"对比失败：{exc}", type="negative")
                return
            # 后台任务中创建 UI 必须进入父容器 slot
            with col, ui.dialog() as dlg, ui.card().classes("w-full max-w-3xl"):
                ui.label("候选人横向对比").classes("text-h6 font-bold mb-2")
                missing = data.get("missing_report", [])
                if missing:
                    ui.label(f"以下候选人暂无评价报告：{', '.join(missing)}").classes(
                        "text-sm text-orange-7 mb-1"
                    )
                dims = data.get("dimensions", [])
                table_data = data.get("score_table", [])
                if table_data:
                    headers = ["候选人", "综合分"] + dims
                    rows_ui = [
                        [
                            row.get("name", ""),
                            str(row.get("overall_score") or "—"),
                        ]
                        + [str(row.get("dimensions", {}).get(d) or "—") for d in dims]
                        for row in table_data
                    ]
                    ui.table(
                        columns=[{"name": h, "label": h, "field": h} for h in headers],
                        rows=[dict(zip(headers, r, strict=False)) for r in rows_ui],
                    ).classes("w-full text-sm")
                summary = data.get("llm_summary", "")
                if summary:
                    ui.label("AI 对比摘要").classes("text-sm font-bold mt-3 mb-1")
                    ui.label(summary).classes("text-sm text-grey-8 whitespace-pre-wrap")
                ui.button("关闭", on_click=dlg.close).classes("mt-2")
            dlg.open()

        def _refresh_compare_bar() -> None:
            compare_bar.clear()
            sel = state["selected_for_compare"]
            with compare_bar:
                if len(sel) >= 2:
                    lbl = f"已选 {len(sel)} 人"
                    ui.label(lbl).classes("text-xs text-grey-7")
                    ui.button(
                        "横向对比",
                        on_click=lambda: asyncio.create_task(_do_compare()),
                    ).props("icon=compare_arrows dense flat color=blue-7").classes(
                        "text-xs"
                    )
                    ui.button(
                        "清除",
                        on_click=lambda: (
                            state["selected_for_compare"].clear(),
                            _render_candidate_list(
                                col,
                                candidates,
                                state,
                                chat_col,
                                chat_scroll,
                                q_col,
                                profile_col,
                                panels,
                                tab_profile,
                                stage_badge,
                                candidate_label,
                                round_label,
                                r_col=r_col,
                                tab_r=tab_r,
                                qs_col=qs_col,
                            ),
                        ),
                    ).props("dense flat color=grey-6").classes("text-xs")

        _refresh_compare_bar()

        for c in candidates:
            cid = c.get("id", "")
            name = c.get("name") or "—"
            pos = c.get("current_position") or ""
            skills = c.get("skills", [])
            yoe = c.get("years_of_experience")
            sub_parts = []
            if pos:
                sub_parts.append(pos)
            elif skills:
                sub_parts.append("、".join(skills[:2]))
            if yoe is not None:
                sub_parts.append(f"{yoe}年")
            subtitle = " · ".join(sub_parts) if sub_parts else "—"
            is_active = state.get("candidate_id") == cid

            item_style = (
                "background:var(--ia-primary-soft); border-radius:8px;"
                if is_active
                else "border-radius:8px;"
            )

            with (
                ui.row()
                .classes(
                    "cand-row w-full items-center gap-2 px-2 py-2 cursor-pointer"
                )
                .style(item_style) as row_el
            ):
                _cb_val = cid in state["selected_for_compare"]

                def _on_cb_change(e, _cid=cid) -> None:
                    if e.value:
                        state["selected_for_compare"].add(_cid)
                    else:
                        state["selected_for_compare"].discard(_cid)
                    _refresh_compare_bar()

                cb = (
                    ui.checkbox(value=_cb_val, on_change=_on_cb_change)
                    .props("dense size=xs")
                    .classes("shrink-0")
                )
                # 阻止点击冒泡到行选中
                cb.on("click.stop", lambda: None)

                # 头像：姓名首字 + 按名字哈希取色
                avatar_colors = (
                    ("#DBEAFE", "#1D4ED8"),
                    ("#DCFCE7", "#15803D"),
                    ("#FEF3C7", "#B45309"),
                    ("#FCE7F3", "#BE185D"),
                    ("#EDE9FE", "#6D28D9"),
                    ("#CFFAFE", "#0E7490"),
                )
                bg_c, fg_c = avatar_colors[hash(name) % len(avatar_colors)]
                with (
                    ui.element("div")
                    .classes(
                        "flex items-center justify-center shrink-0 select-none"
                    )
                    .style(
                        f"width:30px; height:30px; border-radius:8px;"
                        f"background:{bg_c}; color:{fg_c};"
                        "font-size:13px; font-weight:600;"
                    )
                ):
                    ui.label(name[0] if name != "—" else "?")

                with ui.column().classes("flex-1 gap-0 min-w-0"):
                    ui.label(name).classes("text-[13px] font-semibold truncate").style(
                        "color:var(--ia-primary);"
                        if is_active
                        else "color:var(--ia-text);"
                    )
                    ui.label(subtitle).classes("text-xs truncate").style(
                        "color:var(--ia-text-3);"
                    )

                delete_btn = (
                    ui.button(icon="delete_outline")
                    .props("flat dense round color=grey-5 size=xs")
                    .classes("cand-del shrink-0")
                    .tooltip("删除候选人")
                )

                _cid = cid

                async def _on_select_candidate(_cid=_cid) -> None:
                    await _on_candidate_select_inner(
                        _cid,
                        state,
                        chat_col,
                        chat_scroll,
                        q_col,
                        profile_col,
                        panels,
                        tab_profile,
                        stage_badge,
                        candidate_label,
                        round_label,
                        col,
                        candidates,
                        r_col=r_col,
                        tab_r=tab_r,
                        qs_col=qs_col,
                    )

                async def _on_delete_candidate(_cid=_cid, _name=name) -> None:
                    confirmed = await _confirm_delete_dialog(_name, parent=col)
                    if not confirmed:
                        return
                    try:
                        async with httpx.AsyncClient(timeout=10) as client:
                            r = await client.delete(
                                f"{_base_url}/api/candidates/{_cid}"
                            )
                            if r.status_code == 409:
                                detail = r.json().get("detail", {})
                                with col:
                                    ui.notify(
                                        detail.get("message", "无法删除"),
                                        type="warning",
                                    )
                                return
                            r.raise_for_status()
                    except Exception as exc:
                        with col:
                            ui.notify(f"删除失败：{exc}", type="negative")
                        return
                    if state.get("candidate_id") == _cid:
                        state["candidate_id"] = None
                        state["candidate_name"] = "—"
                        _refresh_bar(stage_badge, candidate_label, round_label, state)
                        profile_col.clear()
                        q_col.clear()
                        if qs_col is not None:
                            qs_col.clear()
                    state["candidates"] = [
                        x for x in state["candidates"] if x.get("id") != _cid
                    ]
                    _render_candidate_list(
                        col,
                        state["candidates"],
                        state,
                        chat_col,
                        chat_scroll,
                        q_col,
                        profile_col,
                        panels,
                        tab_profile,
                        stage_badge,
                        candidate_label,
                        round_label,
                        r_col=r_col,
                        tab_r=tab_r,
                        qs_col=qs_col,
                    )
                    with col:
                        ui.notify(f"已删除候选人「{_name}」", type="positive")

                row_el.on(
                    "click",
                    lambda _cid=_cid: asyncio.create_task(_on_select_candidate(_cid)),
                )
                delete_btn.on(
                    "click.stop",
                    lambda _cid=_cid, _name=name: asyncio.create_task(
                        _on_delete_candidate(_cid, _name)
                    ),
                )


async def _on_candidate_select_inner(
    cid: str,
    state: dict,
    chat_col,
    chat_scroll,
    q_col,
    profile_col,
    panels,
    tab_profile,
    stage_badge,
    candidate_label,
    round_label,
    list_col,
    candidates: list,
    r_col=None,
    tab_r=None,
    qs_col=None,
) -> None:
    """点击候选人后调用 /api/candidate/select 更新上下文。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{_base_url}/api/candidate/select",
                json={"candidate_id": cid},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        _error(chat_col, f"选择候选人失败：{exc}")
        await _scroll(chat_scroll)
        return

    profile = data.get("profile", {})
    brief = data.get("brief", "")
    resume_markdown = data.get("resume_markdown", "")
    eval_report = data.get("eval_report")

    state["candidate_id"] = cid
    state["candidate_name"] = profile.get("name") or "—"
    _refresh_bar(stage_badge, candidate_label, round_label, state)

    _render_candidate_list(
        list_col,
        candidates,
        state,
        chat_col,
        chat_scroll,
        q_col,
        profile_col,
        panels,
        tab_profile,
        stage_badge,
        candidate_label,
        round_label,
        r_col=r_col,
        tab_r=tab_r,
        qs_col=qs_col,
    )

    skills = ", ".join((profile.get("skills") or [])[:8])
    pos = profile.get("current_position") or ""
    yoe = profile.get("years_of_experience")
    meta_parts = []
    if pos:
        meta_parts.append(f"职位：{pos}")
    if yoe is not None:
        meta_parts.append(f"工作年限：{yoe} 年")
    if skills:
        meta_parts.append(f"技能：{skills}")
    meta_str = "\n".join(meta_parts) if meta_parts else "—"

    reply = f"已选择候选人：{profile.get('name', '—')}\n" f"{meta_str}\n\n" + (
        "已有面试简报，可在「简报」Tab 查看。" if brief else "暂无面试简报。"
    )
    _bubble(chat_col, reply, sent=False, name="Agent")
    _render_brief(q_col, brief)
    if eval_report and r_col is not None:
        _render_report(r_col, eval_report)
    _render_profile_tab(profile_col, profile, brief, resume_markdown)
    if qs_col is not None:
        await _sync_questions_panel(cid, qs_col)
    panels.set_value(tab_profile)
    await _scroll(chat_scroll)


async def _sync_questions_panel(cid: str, qs_col) -> None:
    """拉取候选人的结构化问题清单并刷新「问题」Tab。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{_base_url}/api/interview/questions",
                params={"candidate_id": cid},
            )
            if r.status_code != 200:
                return
            questions = r.json().get("questions", [])
    except Exception as exc:
        logger.debug("sync_questions_panel failed: %s", exc)
        return
    _render_questions(qs_col, questions, cid)


async def _confirm_delete_dialog(candidate_name: str, *, parent) -> bool:
    dialog_done: asyncio.Future[bool] = asyncio.get_event_loop().create_future()

    # 后台任务中创建 UI 必须进入父容器 slot，否则触发 NiceGUI slot stack 为空错误
    with parent:
        with ui.dialog() as dialog, ui.card().classes("p-4 gap-3"):
            ui.label(f"确认删除「{candidate_name}」？").classes(
                "text-base font-semibold"
            )
            ui.label("将同时删除简历文件、面试记录和评价报告，无法恢复。").classes(
                "text-sm text-grey-7"
            )
            with ui.row().classes("w-full justify-end gap-2 mt-2"):

                def _cancel():
                    if not dialog_done.done():
                        dialog_done.set_result(False)
                    dialog.close()

                def _confirm():
                    if not dialog_done.done():
                        dialog_done.set_result(True)
                    dialog.close()

                ui.button("取消", on_click=_cancel).props("flat dense")
                ui.button("删除", on_click=_confirm).props(
                    "unelevated dense color=negative"
                )

    dialog.open()
    return await dialog_done


def _render_profile_tab(
    col, profile: dict, brief: str = "", resume_markdown: str = ""
) -> None:
    col.clear()
    if not profile:
        with col:
            _panel_placeholder("person", "选择左侧候选人后，\n简历详情将显示在这里")
        return

    with col:
        with (
            ui.card()
            .classes("w-full p-3 gap-1")
            .style(
                "border:1px solid var(--ia-border); border-radius:12px;"
                "box-shadow:var(--ia-shadow-sm);"
            )
        ):
            name = profile.get("name") or "—"
            ui.label(name).classes("text-base font-bold").style(
                "color:var(--ia-text);"
            )
            pos = profile.get("current_position")
            yoe = profile.get("years_of_experience")
            meta_parts = []
            if pos:
                meta_parts.append(pos)
            if yoe is not None:
                meta_parts.append(f"{yoe} 年经验")
            if meta_parts:
                ui.label(" · ".join(meta_parts)).classes("text-sm").style(
                    "color:var(--ia-text-2);"
                )
            skills = profile.get("skills") or []
            if skills:
                with ui.row().classes("flex-wrap gap-1 mt-1"):
                    for s in skills[:15]:
                        ui.label(s).classes("text-xs px-2 py-0.5").style(
                            "background:var(--ia-primary-soft);"
                            "color:var(--ia-primary); border-radius:999px;"
                        )

        if resume_markdown:
            with (
                ui.expansion("简历详情", icon="description", value=True)
                .props("dense")
                .classes("w-full")
                .style(
                    "background:white; border:1px solid var(--ia-border);"
                    "border-radius:10px;"
                )
            ):
                ui.markdown(resume_markdown).classes("text-sm panel-md").style(
                    "color:var(--ia-text-2);"
                )

        if brief:
            with (
                ui.expansion("面试简报", icon="article")
                .props("dense")
                .classes("w-full")
                .style(
                    "background:white; border:1px solid var(--ia-border);"
                    "border-radius:10px;"
                )
            ):
                ui.markdown(brief).classes("text-sm panel-md").style(
                    "color:var(--ia-text-2);"
                )
