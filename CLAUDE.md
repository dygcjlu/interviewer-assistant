# 面试助手 — 项目概要（for 开发 Agent）

## 项目定位

本地单用户技术面试辅助工具。实时采集面试双方音频 → 自动转写 → AI 生成追问建议。无音频时可通过 WebSocket `manual_input` 手动输入完成全流程验证。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12+，asyncio 单进程，FastAPI + uvicorn |
| 前端 | Vue 3 + Pinia + Vue Router（Vite 开发；生产由 FastAPI 托管 `frontend/dist`） |
| LLM | OpenAI 兼容 SDK（`src/llm/client.py`），`base_url` 切换通义/DeepSeek 等 |
| 语音 | Windows：WASAPI 双声道 + 百度实时 ASR；非 Windows：Mock 实现 |
| 存储 | SQLite（aiosqlite），`recordings/` 存录音 |

---

## 系统分层

```
Web 层       FastAPI REST + WebSocket（/api/*、/ws/interview）
Agent 层     Orchestrator + ResumeAgent / InterviewAgent / EvalAgent
框架层       SkillLoader / ToolRegistry / ContextManager / PromptBuilder
存储层       MemoryModule → SQLite Repository
基础设施层   LLMClient / STTEngine / AudioCapturer / AudioRecorder
```

依赖方向：基础设施 → 框架 → Agent → Web；下层不感知上层。

---

## 目录结构

```
src/
├── main.py               # 启动入口，bootstrap() 手动组装依赖
├── config.py             # pydantic-settings，从 .env 加载，get_settings() 单例
├── logging/              # setup_logging (config.py) + contextvars (context.py)
├── web/
│   ├── app.py            # FastAPI 应用 + 静态文件托管
│   ├── routes.py         # REST API
│   ├── websocket.py      # /ws/interview
│   └── middleware.py
├── agents/               # BaseAgent + Orchestrator + 三个 Agent
├── framework/            # skill_loader、tool_registry、context_manager、prompt_builder
├── llm/                  # OpenAI 兼容客户端
├── audio/                # wasapi、baidu_stt、mock、manager、transcription、trigger
├── tools/                # resume_parser、skill_tools
├── storage/              # database、repositories、memory_module
└── models/               # session、candidate、evaluation、message、exceptions
frontend/src/
├── views/                # HomeView / PrepareView / ConsoleView / ReportView
├── stores/interview.js   # Pinia：WS 连接 + 面试状态
└── api/index.js          # REST 封装
skills/                   # SKILL.md 技能文件
tests/
├── conftest.py
├── {test_agents,test_web,...}/  # 单元测试，与 src/ 结构对应
└── e2e/e2e_test.py       # E2E（API + WebSocket）
docs/arc/                 # 详细架构文档
```

---

## 核心模块

**Orchestrator**：管理 `InterviewSession` 生命周期与三 Agent 切换（`resume → interview → eval`）。切换前置条件：`interview` 需 `candidate.id` 存在；`eval` 需 `len(session.rounds) >= 1`（切换前 `flush_pending_round()`）。支持多客户端 WS 广播（`attach_ws_sender / _broadcast`）。

**ResumeAgent**：`parse_resume` 用工具读 PDF → `CandidateProfile`；`generate_questions` 直接调 LLM 返回 JSON 数组题目。上传流程：解析 → 持久化候选人 → 出题 → 写入 `session.question_plan`。

**InterviewAgent**：流式生成追问建议，推送 `suggestion_delta / suggestion_final`。`SuggestionTrigger`：候选人 final segment 后沉默 ~2s 自动触发，或 WS `request_suggestion` 手动触发。

**EvalAgent**：基于 `session.rounds` 生成 `EvalReport`（`dimensions`、`overall_score`、`recommendation`）。由 `POST /api/interview/stop` 触发切换，`GET /api/interview/eval` 返回报告。

**TranscriptionManager**（`src/audio/transcription.py`）：STT / 手动输入与上层的中间层。候选人 final segment 后触发建议；面试官 final segment 且有候选人文本时 `finalize_round()`；候选人沉默 60s 强制归档。`finalize_round()` 写入 `session.rounds` 并广播 `session_snapshot`。

**ContextManager + PromptBuilder**：固定区 + 摘要区 + 滑动窗口；超阈值后台压缩。PromptBuilder 是唯一组装 `list[Message]` 的模块。

**MemoryModule**：短期为运行时 `InterviewSession`；长期为 SQLite（候选人、面试、轮次、报告）。`GET /api/resume/profile` 优先返回当前会话数据，否则从 DB 恢复最近一次面试。

---

## 配置（`.env`）

| 变量 | 说明 | 默认 |
|------|------|------|
| `QWEN_API_KEY` | LLM API Key | 空 |
| `QWEN_API_BASE_URL` | OpenAI 兼容端点 | 通义 compatible-mode |
| `QWEN_MODEL` | 模型名 | `qwen-plus` |
| `HOST` / `PORT` | 后端监听 | `127.0.0.1` / `8000` |
| `DB_PATH` | SQLite 路径 | `interview_assistant.db` |
| `RECORDINGS_DIR` | 录音目录 | `recordings` |
| `CONTEXT_*` | 上下文窗口与 token 预算 | 见 `config.py` |

> 本地开发若 `PORT=8001`，须与 `frontend/vite.config.js` 代理目标一致。

---

## 启动

```powershell
# 后端（Conda 环境：interview-assistant）
conda activate interview-assistant
python -m src.main

# 前端开发（代理 /api、/ws → 后端）
cd frontend && npm run dev

# 生产（FastAPI 托管 dist）
cd frontend && npm run build
python -m src.main

# E2E 验证
python tests/e2e/e2e_test.py   # 默认连 http://127.0.0.1:8001
```

---

## API 与 WebSocket

### REST（前缀 `/api`）

| 路径 | 说明 |
|------|------|
| `POST /resume/upload` | PDF 上传 → 画像 + 题目 |
| `GET /resume/profile` | 候选人画像 + 题目（含 DB 恢复） |
| `GET/PUT /interview/questions` | 读写当前会话题目 |
| `POST /interview/start` | 切换 interview Agent，启动音频 |
| `POST /interview/stop` | 切换 eval Agent |
| `GET /interview/eval` | 生成/获取评价报告 |
| `POST /session/switch` | 通用 Agent 切换 |
| `GET /session/current` | 当前会话快照 |
| `GET /candidates` | 候选人列表 |
| `GET /candidates/{id}/history` | 历史面试 |

### WebSocket `/ws/interview`

**服务端推送**：`session_snapshot`、`transcript`、`suggestion_delta`、`suggestion_final`、`status`、`error`、`heartbeat`

**客户端发送**：

| type | 说明 |
|------|------|
| `manual_input` | `{ source: "candidate"\|"interviewer", text }` |
| `request_suggestion` | 手动触发追问 |
| `set_trigger_mode` | `{ mode: "auto"\|"manual" }` |
| `switch_agent` | `{ target_agent }` |
| `heartbeat` | 保活 |

---

## 关键约定与代码规范

- **Agent 框架自建**，不引入 LangGraph / AutoGen。
- **音频启动失败不阻断面试**：`switch_agent("interview")` 捕获异常后继续，手动输入仍可用。
- **单进程 asyncio**：IO 均为 `async def`；音频帧回调用 `run_coroutine_threadsafe` 桥接；不得调用阻塞 IO。
- **异常**：业务异常定义在 `src/models/exceptions.py`；禁止裸 `except:`。
- **日志**：统一 `logging`，禁止 `print()`；级别：DEBUG / INFO / WARNING / ERROR。
- **文件大小**：Python 文件 ≤ 1000 行，函数 ≤ 100 行，参数 ≤ 5 个。
- **类型标注**：所有函数签名须完整标注，禁止裸 `Any`。
- **测试**：新增核心逻辑须附带单元测试，放 `tests/` 对应目录；E2E 见 `docs/测试需求.md`。
- **密钥**：API Key 仅存 `.env`（已 gitignore），勿提交到仓库。
- 各子模块补充说明见 `src/*/CLAUDE.md`；详细架构见 `docs/arc/`。
