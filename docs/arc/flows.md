# 主要功能流程

五条核心功能的时序图与说明。

---

## 1. PDF 简历上传与解析

简历上传分两步：**① 文件保存**（REST API 直接处理）、**② 解析与题目生成**（面试官通过聊天触发 MainAgent）。

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
    REST->>REST: 去重检查：memory.get_candidate_by_name(safe_stem)
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

    MA->>LLM: chat(messages) — 再次触发 function calling
    LLM-->>MA: tool_call: dispatch_to_agent(agent="resume", task="生成题目...")
    MA->>DA: dispatch_to_agent("resume", task)
    DA->>RA: resume_agent.execute(enriched_task)
    Note over RA: 读取 candidates/{id}/profile.md，生成 8-12 道题
    RA-->>DA: {"type": "questions_done", "questions": [...]}
    DA->>DA: 更新 session.question_plan
    DA->>MA: set_candidate_context(profile, questions)
    DA->>MM: start_interview(session)
    Note over MM: 写 session.json + questions.md
    DA-->>MA: JSON result

    MA->>LLM: chat_stream(messages) — 继续输出解析摘要
    LLM-->>REST: 流式 SSE delta
    REST-->>UI: SSE stream（解析摘要 + 题目清单）
```

**关键数据流转**：

- 上传 API 通过 `get_candidate_by_name(safe_stem)` 检查同名候选人（文件名去扩展名 = 候选人姓名），存在则返回 409
- 解析由面试官在聊天框告知 MainAgent，MainAgent 通过 `dispatch_to_agent` 工具委托 ResumeAgent 执行（两步：先解析，再生成题目）
- `dispatch_to_agent` 自动注入 session 上下文（候选人 ID、profile.md 路径等），避免 ResumeAgent 猜测错误路径
- `parse_done` 副作用：读取临时 Markdown 文件 → `save_candidate()` → 删除临时文件；`questions_done` 副作用：更新 session + `start_interview()`

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
    Note over IC: 检查前置条件：session.candidate.id 非空
    IC->>IA: on_activate(session)
    IA->>IA: 初始化 SuggestionTrigger，创建 ConversationLogger
    IC->>IC: 注册 context_manager._on_compress_done 回调
    IC->>AM: start(session, ws_sender, suggestion_trigger, on_round_finalized)
    Note over AM: MOCK_AUDIO=true → MockAudioManager（脚本回放）<br/>Windows + STT_ENGINE=xunfei → XunfeiRealtimeSTT<br/>Windows（默认）→ BaiduRealtimeSTT<br/>其他平台 → MockSTTEngine
    AM-->>IC: 启动成功（失败则记录警告，继续）
    IC->>IC: session.stage = "interviewing"
    IC->>MM: start_interview(session) [写 session.json stage=interviewing]
    REST-->>UI: {session_id, stage: "interviewing"}
    IC->>WS: broadcast session_snapshot
```

**关键数据流转**：

- `on_activate()` 时 `InterviewAgent` 创建新的 `SuggestionTrigger` 实例和会话级 `ConversationLogger`
- `context_manager._on_compress_done` 回调注册：压缩完成时自动将 summary 同步到 `session.context_summary`
- 音频模式由 `MOCK_AUDIO` 和 `STT_ENGINE` 配置决定；音频启动失败不阻断面试，`stage` 仍切换为 `interviewing`
- `memory.start_interview(session)` 写 `session.json`（stage=interviewing）；`questions.md` 在 `dispatch_to_agent` questions_done 时已写入

---

## 3. 实时转写与追问建议（自动触发）

```mermaid
sequenceDiagram
    participant HW as 麦克风/扬声器
    participant CAP as WasapiCapturer
    participant STT as BaiduRealtimeSTT / XunfeiRealtimeSTT
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
        TM->>TM: 重置静默计时器（60s 超时后强制 finalize_round）
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
- `TranscriptionManager` 是 STT 结果和上层 Agent 的缓冲层：累积转写文本，管理轮次归档
- 轮次归档触发条件：面试官新 segment 到来且候选人已有文字时，自动调用 `finalize_round()`
- 追问建议基于 `session.rounds[-1]`（最近一轮的面试官问题 + 候选人回答）生成

---

## 4. 手动输入 fallback

```mermaid
sequenceDiagram
    participant Client as WebSocket 客户端
    participant WS as websocket.py
    participant IC as InterviewController
    participant TM as TranscriptionManager
    participant WS2 as WebSocket clients（广播）
    participant Trig as SuggestionTrigger
    participant IA as InterviewAgent
    participant LLM as LLMClient

    Client->>WS: {type:"manual_input", source:"interviewer", text:"请介绍一下你的项目经验"}
    WS->>IC: controller.transcription_manager
    WS->>TM: on_segment(TranscriptSegment(source="interviewer", text, is_final=True))
    TM->>WS2: broadcast {type:"transcript", source:"interviewer", text, is_final:true}
    TM->>TM: interviewer_text += text（无候选人文字则不触发 finalize）

    Client->>WS: {type:"manual_input", source:"candidate", text:"我负责了..."}
    WS->>TM: on_segment(TranscriptSegment(source="candidate", text, is_final=True))
    TM->>WS2: broadcast {type:"transcript", source:"candidate", text, is_final:true}
    TM->>TM: candidate_text += text
    TM->>Trig: on_candidate_segment(segment)
    WS->>TM: flush_pending_round() [source="candidate" 时主动触发]
    TM->>TM: finalize_round() → session.rounds.append(round)
    TM->>WS2: broadcast session_snapshot
    alt trigger_mode == "auto"
        Trig->>IA: _on_trigger_fired(request_id)
        IA->>LLM: chat_stream(messages)
        loop 流式 token
            LLM-->>IA: StreamChunk(delta)
            IA->>WS2: {type:"suggestion_delta", request_id, delta}
        end
        IA->>WS2: {type:"suggestion_final", request_id}
    end
```

**关键数据流转**：

- `websocket.py` 通过 `controller.transcription_manager` 获取 `TranscriptionManager`，构造 `TranscriptSegment(is_final=True)` 直接注入，与音频转写走相同路径
- `source="candidate"` 时 `websocket.py` 主动调用 `flush_pending_round()`，确保轮次及时归档（音频路径依赖静默超时，手动路径不依赖）
- 整个追问建议生成链路与音频路径完全相同

---

## 5. 面试结束与评价生成

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
    Note over MM: 写 transcript.md<br/>更新 session.json（end_time/stage/录音路径）<br/>更新两级 index.md
    IC->>IC: context_manager.reset()
    IC->>IC: _session = None
    REST-->>UI: {report: {...}}
```

**关键数据流转**：

- `stop_interview()` 先 `flush_pending_round()` 确保最后一段不丢失，再停止 InterviewAgent 和音频
- 录音路径从 `audio.stop()` 返回，写入 `session.metadata`，由 `finish_interview()` 持久化到 `session.json`
- 路由层**不再在调用 EvalAgent 前主动 `save_interview`**；面试数据由 `close_session()` → `memory.finish_interview()` 统一写入
- `finish_interview()` 同时更新两级 index：`interviews/index.md`（含评价后的评分）和 `candidates/index.md`（更新 latest_interview）
- EvalAgent 不再调用 `consolidate_memory`（旧版更新 `last_interview_insights` 的逻辑已移除，历史摘要改由 `interviews/index.md` 中的 `key_findings` 字段承载）
