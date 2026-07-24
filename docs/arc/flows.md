# 主要功能流程

五条核心功能的时序图与说明。

---

## 1. PDF 简历上传与解析

简历上传分两步：**① 文件保存**（REST API 直接处理）、**② 解析与简报生成**（面试官通过与 MainAgent 对话触发）。

```mermaid
sequenceDiagram
    participant UI as NiceGUI UI
    participant REST as routes.py
    participant IC as InterviewController
    participant MA as MainAgent
    participant DA as dispatch_to_agent
    participant RA as ResumeAgent
    participant LLM as LLMClient
    participant MM as MemoryModule

    UI->>REST: POST /api/resume/upload (file, candidate_id?)
    REST->>IC: get_session() / create_session() [确保会话存在]
    REST->>REST: 保存 PDF 到 resumes/{safe_stem}.pdf
    REST-->>UI: {file_path, safe_stem, session_id, candidate_id}

    UI->>REST: POST /api/chat {"message": "请解析简历 {file_path}"}
    REST->>MA: handle_chat("请解析简历 {file_path}")
    MA->>LLM: chat(messages) — 触发 function calling
    LLM-->>MA: tool_call: dispatch_to_agent(agent="resume", task="解析...")
    MA->>DA: dispatch_to_agent("resume", task)
    DA->>DA: _enrich_task_with_session_context() — 注入候选人 ID/路径信息
    DA->>RA: resume_agent.execute(enriched_task)
    Note over RA: ReAct 模式：调用 parse_resume_pdf / file_write
    RA-->>DA: {"type": "parse_done", "profile": {...}, "markdown_path": "resumes/张三.md"}
    DA->>MM: save_candidate(session.candidate, resume_markdown)
    Note over MM: 写 candidates/{id}/profile.md<br/>更新 candidates/index.md
    DA->>DA: session.candidate.resume_content = resume_markdown
    DA-->>MA: JSON result

    MA->>LLM: chat_stream(messages) — 呈现候选人分析（阶段一）
    LLM-->>REST: 流式 SSE delta（候选人概况 + 风险信号 + 建议关注方向）
    REST-->>UI: SSE stream

    Note over MA,UI: 阶段二：2-4 轮对话收集面试官关注点
    UI->>REST: POST /api/chat {"message": "重点考察稳定性和系统设计..."}
    REST->>MA: handle_chat(...)
    MA->>LLM: chat_stream(messages) — 确认关注点，提议生成简报
    LLM-->>REST: SSE delta

    UI->>REST: POST /api/chat {"message": "好，生成简报"}
    REST->>MA: handle_chat(...)
    MA->>LLM: chat(messages) — 触发 function calling
    LLM-->>MA: tool_call: dispatch_to_agent(agent="resume", task="生成面试简报，关注点：...")
    MA->>DA: dispatch_to_agent("resume", task)
    DA->>RA: resume_agent.execute(enriched_task)
    Note over RA: 读取 candidates/{id}/profile.md，生成结构化简报
    RA-->>DA: {"type": "brief_done", "candidate_id": "...", "brief": "<Markdown>"}
    DA->>MM: save_brief(candidate_id, brief_text)
    Note over MM: 写 candidates/{id}/brief.md
    DA->>DA: asyncio.create_task(_generate_questions_from_brief)
    Note over DA: 异步生成 questions.json，不阻塞主流程
    DA->>DA: session.interview_brief = brief_text
    DA->>MA: set_candidate_context(profile, interview_brief, history_summary?)
    DA-->>MA: JSON result
    Note over IC: session.stage 仍为 IDLE<br/>用户需点击「开始面试」

    MA->>LLM: chat_stream(messages) — 简报生成完成提示
    LLM-->>REST: 流式 SSE delta
    REST-->>UI: SSE stream
```

**关键数据流转**：

- 解析由面试官聊天触发 MainAgent，MainAgent 通过 `dispatch_to_agent` 工具委托 ResumeAgent 执行
- 解析完成后 MainAgent 进入两阶段引导：① 呈现分析 + 风险信号；② 对话收集关注点后生成简报
- `dispatch_to_agent` 自动注入 session 上下文（候选人 ID、profile.md 路径、brief.md 路径等）
- `parse_done` 副作用：解析出 `real_name` 后**先判重再落盘**；若与已有候选人同名，将 profile + resume_markdown 暂存到进程内 `pending_duplicates`，通过 SSE `duplicate_candidate` 事件通知前端三选一（覆盖 / 保留两份 / 取消），由 `POST /api/resume/resolve-duplicate` 执行决议；未命中重名则正常 `save_candidate()`
- `brief_done` 副作用：`save_brief()` 落盘 → 异步生成结构化问题清单（`questions.json`）→ 更新 `session.interview_brief` → 刷新 MainAgent Layer 3；**不写 `session.json`，`session.stage` 维持 IDLE**；用户需显式 `POST /api/interview/start`

---

## 2. 面试开始

```mermaid
sequenceDiagram
    participant UI as NiceGUI UI
    participant REST as routes.py
    participant IC as InterviewController
    participant IA as InterviewAgent
    participant AM as AudioManager / MockAudioManager
    participant MM as MemoryModule
    participant WS as WebSocket clients

    UI->>REST: POST /api/interview/start {candidate_id, trigger_mode}
    REST->>IC: get_session() [若无会话则 create_session]
    REST->>IC: start_interview()
    Note over IC: 前置条件：stage=IDLE 且 candidate.id 非空
    IC->>IA: on_activate(session)
    IA->>IA: 初始化 SuggestionTrigger，创建 ConversationLogger
    IC->>IC: context_manager.set_compress_done_handler(...)
    IC->>AM: start(session, ws_sender, suggestion_trigger, on_round_finalized)
    Note over AM: MOCK_AUDIO / STT_ENGINE=baidu|xunfei|volc / 非 Windows Mock
    AM-->>IC: 启动成功（失败则 audio_status ok=false，继续）
    Note over IC: on_round_finalized：add_round + append_round(WAL) + 自动覆盖检测
    IC->>IC: session.stage = "interviewing"
    IC->>MM: start_interview(session) [写 session.json]
    REST-->>UI: {session_id, stage: "interviewing"}
    IC->>WS: broadcast session_snapshot
```

**关键数据流转**：

- `on_activate()` 时 `InterviewAgent` 创建新的 `SuggestionTrigger` 与会话级 `ConversationLogger`
- 压缩完成回调经 `ContextManager.set_compress_done_handler` 同步到 `session.context_summary`
- 音频模式由 `MOCK_AUDIO` 与 `STT_ENGINE`（`baidu` / `xunfei` / `volc`）决定；音频启动失败不阻断面试，经 WebSocket 推送 `audio_status`
- 每轮 `finalize_round` 后由 `on_round_finalized`：① `ContextManager.add_round`；② `memory.append_round`（WAL）；③ 异步 `_auto_check_coverage`（问题覆盖检测）
- **`memory.start_interview(session)` 在 Controller `start_interview()` 内调用**（写 `session.json`，stage=interviewing），与 brief 生成解耦

---

## 3. 实时转写与追问建议（自动触发）

```mermaid
sequenceDiagram
    participant HW as 麦克风/扬声器
    participant CAP as WasapiCapturer
    participant STT as Baidu / Xunfei / Volc STT
    participant TM as TranscriptionManager
    participant WS as WebSocket clients
    participant Trig as SuggestionTrigger
    participant IA as InterviewAgent
    participant LLM as LLMClient

    HW->>CAP: 音频帧（每 20ms）
    CAP->>STT: send_audio(pcm_bytes)
    STT->>STT: 实时语音识别
    STT-->>TM: TranscriptSegment(source, text, is_final)
    TM->>WS: broadcast {type:"transcript", source, text, is_final}
    alt is_final == true
        TM->>TM: 重置静默计时器（超时后强制 finalize_round）
        alt source == "candidate"
            TM->>TM: candidate_text += segment.text
            TM->>Trig: on_candidate_segment(segment)
            Note over Trig: 候选人说完后<br/>静默 ~2s 自动触发
            Trig->>IA: _on_trigger_fired(request_id)
            IA->>IA: 取消上一次未完成的流
            IA->>LLM: chat_stream(messages)
            loop 流式 token
                LLM-->>IA: StreamChunk(delta)
                IA->>WS: {type:"suggestion_delta", request_id, delta}
            end
            IA->>WS: {type:"suggestion_final", request_id}
        else source == "interviewer" && candidate_text 非空
            TM->>TM: finalize_round()
            TM->>TM: session.rounds.append(ConversationRound)
            TM->>WS: broadcast session_snapshot (rounds_count 更新)
            TM->>TM: interviewer_text += segment.text
        end
    end
```

**关键数据流转**：

- `WasapiCapturer` 通过 `run_coroutine_threadsafe` 将音频帧回调桥接到 asyncio 事件循环
- `TranscriptionManager` 缓冲 STT 结果、管理轮次归档；归档条件为面试官新 segment 到来且候选人已有文字
- 追问建议基于 PromptBuilder 组装的上下文（含全量/窗口历史）流式生成
- `InterviewAgent.generate_suggestion()` 通过 `chat_stream()` 逐 token yield，WebSocket 推送 `suggestion_delta` / `suggestion_final`；新触发会取消上一次流

---

## 4. 面试结束与评价生成

```mermaid
sequenceDiagram
    participant UI as NiceGUI UI
    participant REST as routes.py
    participant IC as InterviewController
    participant TM as TranscriptionManager
    participant AM as AudioManager / MockAudioManager
    participant EA as EvalAgent
    participant LLM as LLMClient
    participant MM as MemoryModule

    UI->>REST: POST /api/interview/stop
    REST->>IC: get_session()
    REST->>IC: stop_interview()
    IC->>TM: flush_pending_round() [归档尚未归档的最后一轮]
    IC->>IA: on_deactivate(session) [停止 SuggestionTrigger]
    IC->>AM: stop() [停止音频采集和转写，返回录音路径]
    IC->>IC: session.metadata.recording_*_path = rec.*
    alt len(rounds) >= 1
        IC->>IC: session.stage = "evaluating"
    else
        IC->>IC: session.stage = "completed"
    end
    REST-->>UI: {session_id, stage:"evaluating", total_rounds:N}

    UI->>REST: GET /api/interview/eval
    REST->>EA: handle_request("generate_eval")
    EA->>EA: _read_user_memory() — UserMemoryStore.render()
    EA->>EA: _build_base_messages(session, user_memory)
    EA->>EA: 估算 token 数（字符数，中文约 1 char/token）
    alt 估算 token ≤ 30000（单次调用）
        EA->>LLM: chat(base_messages + 全量对话)
        LLM-->>EA: EvalReport JSON（含 evidence 字段）
    else 估算 token > 30000（map-reduce 分块，每 30 轮一块）
        loop 每 30 轮一块
            EA->>LLM: chat(base_messages + 部分轮次)
            LLM-->>EA: 局部分析文字
        end
        EA->>LLM: chat(base_messages + 所有局部分析汇总)
        LLM-->>EA: EvalReport JSON（含 evidence 字段）
    end
    EA->>EA: 构造 EvalReport 对象
    EA->>MM: save_eval_report(report)
    Note over MM: 写 eval_report.md<br/>更新 interviews/index.md（评分+关键结论）
    EA-->>REST: AgentResponse(data={report})
    REST->>IC: close_session()
    IC->>MM: finish_interview(session)
    Note over MM: 写 transcript.md<br/>更新 session.json<br/>归档 rounds.jsonl → .archived<br/>更新两级 index.md
    IC->>IC: context_manager.reset()
    IC->>IC: _session = None
    REST-->>UI: {report: {...}}
```

**关键数据流转**：

- `stop_interview()` 先 `flush_pending_round()`，再停 InterviewAgent 与音频
- 录音路径从 `audio.stop()` 写回 `session.metadata`，由 `finish_interview()` 持久化到 `session.json`
- 路由层不在调用 EvalAgent 前主动 `save_interview`；面试数据由 `close_session()` → `memory.finish_interview()` 统一写入
- `finish_interview()` 更新两级 index，并将 WAL 归档为 `rounds.jsonl.archived`
- EvalAgent 不调用 `consolidate_memory`；历史摘要由 `interviews/index.md` 的 `key_findings` 承载

---

## 5. 问题清单与覆盖检测（简报后）

简报生成后异步产出 `candidates/{id}/questions.json`；面试中每轮归档可自动覆盖检测，也可手动触发。

| 入口 | 行为 |
|---|---|
| `brief_done` → `_generate_questions_from_brief` | LLM 从简报提取 5–10 题，写入 `questions.json` |
| `GET /api/interview/questions` | 读取问题清单 |
| `PATCH /api/interview/questions/{id}` | 手动标记 covered |
| `POST /api/interview/questions/check-coverage` | 用 LLM 根据对话文本标记已覆盖题 |
| `on_round_finalized` → `_auto_check_coverage` | 轮次落盘后自动覆盖检测（静默失败） |
