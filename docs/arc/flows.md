# 主要功能流程

五条核心功能的时序图与说明。

---

## 1. PDF 简历上传与解析

```mermaid
sequenceDiagram
    participant UI as NiceGUI UI
    participant REST as routes.py
    participant Orch as Orchestrator
    participant RA as ResumeAgent
    participant LLM as LLMClient
    participant MM as MemoryModule
    participant DB as SQLite

    UI->>REST: POST /api/resume/upload (file, candidate_id?)
    REST->>Orch: create_session(candidate_id?) [若无活跃会话]
    REST->>Orch: switch_agent("resume")
    Orch->>RA: on_activate(session)
    REST->>REST: 保存 PDF 到 resumes/{session_id}_{ts}.pdf
    REST->>REST: parse_resume_pdf() 预提取文本 → session.candidate.resume_text
    REST->>Orch: handle_request("parse_resume", file_path)
    Orch->>RA: handle_request
    RA->>LLM: chat_with_tools(messages) — 调用 parse_resume_pdf 工具
    LLM-->>RA: 结构化 JSON (name, email, work_experience, skills...)
    RA->>RA: _update_candidate_from_data() → 写入 session.candidate
    RA-->>Orch: AgentResponse(success=True, data={profile_data})
    Orch-->>REST: AgentResponse
    REST->>REST: 保存简历 Markdown → resumes/{session_id}.md
    REST->>MM: save_candidate(session.candidate)
    MM->>DB: INSERT OR REPLACE Candidate
    REST->>Orch: handle_request("generate_questions")
    Orch->>RA: handle_request
    RA->>LLM: chat(messages) — 直接生成题目，无工具调用
    LLM-->>RA: JSON 数组（8-12 道题）
    RA-->>REST: AgentResponse(data={questions})
    REST->>REST: 构造 InterviewQuestion 列表 → session.question_plan
    REST-->>UI: {candidate_id, profile, questions}
```

**关键数据流转**：

- `routes.py` 先做 PDF 文本预提取（`parse_resume_pdf`），将原始文本存入 `session.candidate.resume_text`，之后 `ResumeAgent` 再用 LLM 工具调用做结构化解析
- `ResumeAgent._parse_resume()` 通过 `_run_with_tools()` 循环执行 LLM 工具调用，直到 LLM 停止调用工具
- 题目生成（`generate_questions`）不调用工具，直接 `llm_client.chat()`
- 候选人数据先写内存（`session.candidate`），再异步持久化到 DB

---

## 2. 面试开始

```mermaid
sequenceDiagram
    participant UI as NiceGUI UI
    participant REST as routes.py
    participant Orch as Orchestrator
    participant IA as InterviewAgent
    participant AM as AudioManager
    participant WS as WebSocket clients

    UI->>REST: POST /api/interview/start {candidate_id, trigger_mode}
    REST->>Orch: get_session() [若无会话则 create_session]
    REST->>Orch: switch_agent("interview")
    Note over Orch: 检查前置条件：session.candidate.id 非空
    Orch->>IA: on_deactivate(session) [若有上一个 Agent]
    Orch->>IA: on_activate(session)
    IA->>IA: 初始化 SuggestionTrigger
    Orch->>IA: attach_ws_sender(broadcast)
    Orch->>AM: start(session, ws_sender, suggestion_trigger, on_round_finalized)
    Note over AM: 启动 WasapiCapturer + BaiduRealtimeSTT [Windows]<br/>或 MockAudioCapturer + MockSTTEngine [其他平台, Mock]
    AM-->>Orch: 启动成功（失败则记录警告，继续）
    Orch->>Orch: session.stage = "interviewing"
    REST-->>UI: {session_id, stage: "interviewing"}
    REST->>Orch: handle_request("set_trigger_mode") [若 trigger_mode != "auto"]
    Orch->>IA: handle_request("set_trigger_mode")
    IA->>IA: SuggestionTrigger.set_mode(mode)
    Orch->>WS: broadcast session_snapshot
```

**关键数据流转**：

- `on_activate()` 时 `InterviewAgent` 创建新的 `SuggestionTrigger` 实例，绑定 `_on_trigger_fired` 回调
- 音频启动失败不阻断面试：异常被捕获后记录 `WARNING` 日志，`stage` 仍切换为 `interviewing`，手动输入路径完整可用

---

## 3. 实时转写与追问建议（自动触发）

```mermaid
sequenceDiagram
    participant HW as 麦克风/扬声器
    participant CAP as WasapiCapturer
    participant STT as BaiduRealtimeSTT
    participant TM as TranscriptionManager
    participant WS as WebSocket clients
    participant Trig as SuggestionTrigger
    participant IA as InterviewAgent
    participant LLM as LLMClient

    HW->>CAP: 音频帧（每 20ms）
    CAP->>STT: send_audio(pcm_bytes)
    STT->>STT: 百度实时 ASR 识别
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
    participant Orch as Orchestrator
    participant TM as TranscriptionManager
    participant WS2 as WebSocket clients（广播）
    participant Trig as SuggestionTrigger
    participant IA as InterviewAgent
    participant LLM as LLMClient

    Client->>WS: {type:"manual_input", source:"interviewer", text:"请介绍一下你的项目经验"}
    WS->>Orch: orchestrator.transcription_manager
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

- `websocket.py` 构造 `TranscriptSegment(is_final=True)` 直接注入 `TranscriptionManager`，与音频转写走相同路径
- `source="candidate"` 时 `websocket.py` 主动调用 `flush_pending_round()`，确保轮次及时归档（音频路径依赖静默超时，手动路径不依赖）
- 整个追问建议生成链路与音频路径完全相同

---

## 5. 面试结束与评价生成

```mermaid
sequenceDiagram
    participant UI as NiceGUI UI
    participant REST as routes.py
    participant Orch as Orchestrator
    participant TM as TranscriptionManager
    participant AM as AudioManager
    participant EA as EvalAgent
    participant LLM as LLMClient
    participant MM as MemoryModule
    participant DB as SQLite

    UI->>REST: POST /api/interview/stop
    REST->>Orch: get_session()
    REST->>Orch: switch_agent("eval")
    Note over Orch: 前置检查：len(session.rounds) >= 1
    Orch->>TM: flush_pending_round() [归档尚未归档的最后一轮]
    Orch->>AM: stop() [停止音频采集和转写]
    Orch->>EA: on_activate(session)
    Orch->>Orch: session.stage = "evaluating"
    REST-->>UI: {session_id, stage:"evaluating", total_rounds:N}

    UI->>REST: GET /api/interview/eval
    REST->>MM: save_interview(session) [先持久化，满足 FK 约束]
    MM->>DB: UPSERT Interview + ConversationRound
    REST->>Orch: handle_request("generate_eval")
    Orch->>EA: handle_request
    EA->>EA: 拼接所有 rounds 为对话文本
    EA->>LLM: chat_with_tools(messages, conversation)
    LLM-->>EA: EvalReport JSON
    EA->>EA: 构造 EvalReport 对象
    EA->>MM: save_eval_report(report)
    MM->>DB: INSERT EvalReport
    EA->>EA: asyncio.create_task(consolidate_memory) [后台异步，不阻塞]
    EA-->>REST: AgentResponse(data={report})
    REST->>Orch: close_session()
    Orch->>MM: save_interview(session) [写入 end_time]
    MM->>DB: UPDATE Interview.end_time, context_summary
    REST-->>UI: {report: {...}}
```

**关键数据流转**：

- `switch_agent("eval")` 前先 `flush_pending_round()`，确保候选人最后一段回答不丢失
- `GET /api/interview/eval` 在调用 EvalAgent 前先 `save_interview()`，确保 `EvalReport` 的外键约束（`REFERENCES Interview(id)`）可以满足
- 评价报告生成后，`consolidate_memory()` 在后台更新候选人 `profile_json` 中的 `last_interview_insights` 字段，供下次面试时作为历史上下文
- `close_session()` 将 `session.stage` 设为 `completed`，写入 `end_time`，并重置内存中的会话对象
