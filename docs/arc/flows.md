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
    participant RA as ResumeAgent
    participant LLM as LLMClient
    participant MM as MemoryModule
    participant DB as SQLite

    UI->>REST: POST /api/resume/upload (file, candidate_id?)
    REST->>IC: get_session() / create_session() [确保会话存在]
    REST->>REST: 保存 PDF 到 resumes/{session_id}_{ts}.pdf
    REST->>REST: parse_resume_pdf() 预提取文本 → session.candidate.resume_text
    REST->>REST: 保存简历 Markdown → resumes/{session_id}.md
    REST-->>UI: {file_path, session_id, candidate_id}

    UI->>REST: POST /api/chat {"message": "请解析简历 {file_path}"}
    REST->>MA: handle_chat("请解析简历 {file_path}")
    MA->>LLM: chat_stream(messages) — 触发 function calling
    LLM-->>MA: tool_call: delegate_to_resume_agent(pdf_path, instructions)
    MA->>RA: execute(pdf_path, instructions)
    RA->>LLM: chat_with_tools(messages) — 调用 parse_resume 工具
    LLM-->>RA: 结构化 JSON (name, email, work_experience, skills...)
    RA->>LLM: chat(messages) — 直接生成题目，无工具调用
    LLM-->>RA: JSON 数组（8-12 道题）
    RA-->>MA: {profile, questions}
    MA->>MM: save_candidate(profile)
    MM->>DB: INSERT OR REPLACE Candidate
    MA->>MA: set_candidate_context(profile, questions)
    MA->>LLM: chat_stream 继续输出解析摘要
    LLM-->>REST: 流式 SSE delta
    REST-->>UI: SSE stream（解析摘要 + 题目清单）
```

**关键数据流转**：

- 上传 API 只负责保存文件、预提取文本并写 Markdown，**不触发 LLM 解析**，返回 `file_path`
- 解析由面试官在聊天框告知 MainAgent，MainAgent 通过 `delegate_to_resume_agent` 工具委托 ResumeAgent 执行
- `ResumeAgent.execute()` 内部串行执行 `_parse_resume()` → `_generate_questions()`，两步共享同一 session
- 候选人数据由 MainAgent 写入 SQLite，并更新自身的 candidate context（Layer 3）

---

## 2. 面试开始

```mermaid
sequenceDiagram
    participant UI as NiceGUI UI
    participant REST as routes.py
    participant IC as InterviewController
    participant IA as InterviewAgent
    participant AM as AudioManager / MockAudioManager
    participant WS as WebSocket clients

    UI->>REST: POST /api/interview/start {candidate_id, trigger_mode}
    REST->>IC: get_session() [若无会话则 create_session]
    REST->>IC: start_interview()
    Note over IC: 检查前置条件：session.candidate.id 非空
    IC->>IA: on_activate(session)
    IA->>IA: 初始化 SuggestionTrigger，创建 ConversationLogger
    IC->>AM: start(session, ws_sender, suggestion_trigger, on_round_finalized)
    Note over AM: MOCK_AUDIO=true → MockAudioManager（脚本回放）<br/>Windows → WasapiCapturer + BaiduRealtimeSTT<br/>其他平台 → MockAudioCapturer + MockSTTEngine
    AM-->>IC: 启动成功（失败则记录警告，继续）
    IC->>IC: session.stage = "interviewing"
    REST-->>UI: {session_id, stage: "interviewing"}
    IC->>WS: broadcast session_snapshot
```

**关键数据流转**：

- `on_activate()` 时 `InterviewAgent` 创建新的 `SuggestionTrigger` 实例和会话级 `ConversationLogger`（写入 `conversations/interview_agent_{session_id}.jsonl`）
- 音频模式由 `MOCK_AUDIO` 配置决定：`true` 时使用 `MockAudioManager` 按 `MOCK_AUDIO_SCRIPT` 脚本回放，无需真实麦克风；`false` 时按平台选择
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
    participant IC as InterviewController
    participant TM as TranscriptionManager
    participant WS2 as WebSocket clients（广播）
    participant Trig as SuggestionTrigger
    participant IA as InterviewAgent
    participant LLM as LLMClient

    Client->>WS: {type:"manual_input", source:"interviewer", text:"请介绍一下你的项目经验"}
    WS->>IC: controller.audio_manager.transcription_manager
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

- `websocket.py` 通过 `InterviewController.audio_manager.transcription_manager` 获取 `TranscriptionManager`，构造 `TranscriptSegment(is_final=True)` 直接注入，与音频转写走相同路径
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
    participant DB as SQLite

    UI->>REST: POST /api/interview/stop
    REST->>IC: get_session()
    REST->>IC: stop_interview()
    Note over IC: 前置检查：len(session.rounds) >= 1
    IC->>TM: flush_pending_round() [归档尚未归档的最后一轮]
    IC->>AM: stop() [停止音频采集和转写]
    IC->>IC: session.stage = "evaluating"
    REST-->>UI: {session_id, stage:"evaluating", total_rounds:N}

    UI->>REST: GET /api/interview/eval
    REST->>MM: save_interview(session) [先持久化，满足 FK 约束]
    MM->>DB: UPSERT Interview + ConversationRound
    REST->>EA: handle_request("generate_eval")
    EA->>EA: _read_user_memory() 读取 USER.md
    EA->>EA: 估算 token 数，选择调用路径
    alt 估算 token ≤ 30000（单次调用）
        EA->>LLM: chat(base_messages + 全量对话)
        LLM-->>EA: EvalReport JSON
    else 估算 token > 30000（map-reduce 分块，每 30 轮一块）
        loop 每 30 轮一块
            EA->>LLM: chat(base_messages + 部分轮次)
            LLM-->>EA: 局部分析文字
        end
        EA->>LLM: chat(base_messages + 所有局部分析汇总)
        LLM-->>EA: EvalReport JSON
    end
    EA->>EA: 构造 EvalReport 对象
    EA->>MM: save_eval_report(report)
    MM->>DB: INSERT EvalReport
    EA->>EA: asyncio.create_task(consolidate_memory) [后台异步，不阻塞]
    EA-->>REST: AgentResponse(data={report})
    REST->>IC: close_session()
    IC->>MM: save_interview(session) [写入 end_time]
    MM->>DB: UPDATE Interview.end_time, context_summary
    REST-->>UI: {report: {...}}
```

**关键数据流转**：

- `stop_interview()` 在 `InterviewController` 内部先 `flush_pending_round()`，确保候选人最后一段回答不丢失
- `GET /api/interview/eval` 在调用 EvalAgent 前先 `save_interview()`，确保 `EvalReport` 的外键约束（`REFERENCES Interview(id)`）可以满足
- EvalAgent 自建 messages（不使用 PromptBuilder），每次 eval 直接读取 USER.md 注入岗位要求；根据 token 估算自动选择单次调用或 map-reduce 分块路径
- 评价报告生成后，`consolidate_memory()` 在后台更新候选人 `profile_json` 中的 `last_interview_insights` 字段，供下次面试时作为历史上下文
- `close_session()` 将 `session.stage` 设为 `completed`，写入 `end_time`，并重置内存中的会话对象
