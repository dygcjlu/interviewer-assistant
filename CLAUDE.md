# 面试助手 — 项目概要（for 开发 Agent）

## 项目定位

面向技术面试场景的**本地单用户面试辅助工具**。面试官在本机运行，通过浏览器访问 localhost 使用。

核心价值：实时采集面试双方音频 → 自动转写 → 由 AI 生成追问建议，辅助面试官提问。无音频设备时可通过控制台**手动输入**候选人/面试官文本完成全流程验证。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12+，asyncio 单进程，FastAPI + uvicorn |
| 前端 | Vue 3 + Pinia + Vue Router（Vite 开发；生产由 FastAPI 托管 `frontend/dist`） |
| LLM | OpenAI 兼容 SDK（`src/llm/client.py`），`base_url` 切换通义/DeepSeek 等 |
| 语音 | Windows：WASAPI 双声道 + 百度实时 ASR；非 Windows：`MockAudioCapturer` / `MockSTTEngine` |
| 存储 | SQLite（aiosqlite），`recordings/` 存录音，`trajectories/` 存 Agent 轨迹 JSONL |

---

## 系统分层（依赖方向：下 → 上创建，上 → 下调用）

```
Web 层       FastAPI REST + WebSocket（/api/*、/ws/interview）
Agent 层     Orchestrator + ResumeAgent / InterviewAgent / EvalAgent
框架层       SkillLoader / ToolRegistry / ContextManager / PromptBuilder
存储层       MemoryModule → SQLite Repository
基础设施层   LLMClient / STTEngine / AudioCapturer / AudioRecorder
```

---

## 核心模块速览

### Orchestrator（`src/agents/orchestrator.py`）

- 管理 `InterviewSession` 生命周期与三 Agent 切换（`resume` → `interview` → `eval`）。
- Agent 间**不直接通信**，共享 `InterviewSession`。
- WebSocket 支持**多客户端广播**（`attach_ws_sender` / `_broadcast`）。
- **切换前置条件**：
  - `interview`：候选人 `candidate.id` 已存在。
  - `eval`：`len(session.rounds) >= 1`（切换前会先 `flush_pending_round()`，避免最后一轮未归档）。
- 离开 `interview` 时：`eval` 目标调用 `audio.stop()`（含末轮归档）；其他目标 `audio.pause()`。

### ResumeAgent（`src/agents/resume_agent.py`）

- `parse_resume`：通过 `_run_with_tools()` 调用 `parse_resume` 工具读 PDF，解析为 `CandidateProfile`。
- `generate_questions`：**直接** `llm_client.chat(tools=None)`，将候选人 JSON 写入 user 消息，要求返回 JSON 数组；`_normalize_questions()` 兼容中英文字段名。
- 上传流程（`POST /api/resume/upload`）：`parse_resume` → 持久化候选人 → `generate_questions` → 写入 `session.question_plan`。

### InterviewAgent（`src/agents/interview_agent.py`）

- 流式生成追问建议（`suggestion_delta` / `suggestion_final`）。
- `SuggestionTrigger`：候选人 final segment 后沉默 2 秒自动触发；或 WebSocket `request_suggestion` 手动触发。

### EvalAgent（`src/agents/eval_agent.py`）

- `generate_eval` 基于 `session.rounds` 生成 `EvalReport`（`dimensions`、`overall_score`、`recommendation` 等）。
- 由 `POST /api/interview/stop` 触发切换到 eval，再 `GET /api/interview/eval` 生成并返回报告。

### TranscriptionManager（`src/audio/transcription.py`）

STT / 手动输入与上层的中间层：

| 事件 | 行为 |
|------|------|
| 候选人 final segment | 累积文本；触发 `SuggestionTrigger` |
| 面试官 final segment（且已有候选人文本） | `finalize_round()` 后开始新一轮 |
| 候选人沉默 60s | 强制 `finalize_round()` |
| `flush_pending_round()` | 有未归档内容则归档 |
| `finalize_round()` | 写入 `session.rounds`；回调 `ContextManager.add_round`；广播 `session_snapshot`（含 `rounds_count`） |

**手动输入**（WebSocket `manual_input`）：候选人发言后立即 `flush_pending_round()`，便于轮次统计与结束面试。

### ContextManager + PromptBuilder

- **ContextManager**：固定区 + 摘要区 + 滑动窗口；超阈值后台压缩（不阻塞 `build()`）。
- **PromptBuilder**：唯一组装 `list[Message]` 的模块（system → skill → tool 说明 → 长期记忆 → 固定区 → 摘要 → 滑动窗口）。

### MemoryModule（`src/storage/memory_module.py`）

- 短期：运行时 `InterviewSession`（agents 层持有）。
- 长期：SQLite 候选人、面试记录、轮次、评价报告。
- `save_interview()` 持久化 `question_plan_json`；`get_latest_question_plan()` 供再次打开准备页时恢复题目。
- `GET /api/resume/profile`：优先当前会话 `question_plan`，否则从 DB 最近一次面试恢复。

---

## 目录结构（关键路径）

```
src/
├── main.py               # 启动入口，手动组装依赖；Windows/非 Windows 音频分支
├── config.py             # pydantic Settings，从 .env 加载
├── web/
│   ├── routes.py         # REST API
│   ├── websocket.py      # /ws/interview
│   └── app.py            # FastAPI 应用 + 静态文件托管
├── agents/               # BaseAgent、三 Agent、Orchestrator
├── framework/            # skill、tool_registry、context、prompt_builder
├── llm/                  # OpenAI 兼容客户端
├── audio/                # wasapi、baidu_stt、mock、manager、transcription、trigger
├── tools/                # resume_parser、skill_tools
├── storage/              # database、repositories、memory_module
└── models/               # session、candidate、evaluation、message、exceptions
frontend/
├── src/views/            # Home / Prepare / Console / Report
├── src/stores/interview.js  # WebSocket + 面试状态
└── vite.config.js        # 开发代理 /api、/ws → 后端 PORT
skills/                   # resume_anchor、deep_dive 等 SKILL.md
docs/
├── 项目需求.md
├── 测试需求.md           # 端到端测试计划 T-01 ~ T-10
└── arc/                  # 详细架构文档
e2e_test.py               # 自动化 E2E（API + WebSocket，依赖 test_resume.pdf）
tests/                    # 单元测试（目录与 src 对应）
```

---

## 配置与启动

### 配置（`.env`）

`config.py` 中 `Settings` 从项目根目录 `.env` 读取，常用项：

| 变量 | 说明 | 默认 |
|------|------|------|
| `QWEN_API_KEY` | LLM API Key | 空 |
| `QWEN_API_BASE_URL` | OpenAI 兼容端点 | 通义 compatible-mode |
| `QWEN_MODEL` | 模型名 | `qwen-plus` |
| `HOST` / `PORT` | 后端监听 | `127.0.0.1` / `8000` |
| `DB_PATH` | SQLite 路径 | `interview_assistant.db` |
| `RECORDINGS_DIR` | 录音目录 | `recordings` |
| `CONTEXT_*` | 上下文窗口与 token 预算 | 见 `config.py` |

> 本地开发若 `.env` 中 `PORT=8001`，须与 `frontend/vite.config.js` 代理目标一致。

### 后端

```powershell
# 推荐 Conda 环境名：interview-assistant
conda activate interview-assistant
cd d:\interview\interviewer-assistant
pip install -r requirements.txt
python -m src.main
```

### 前端（开发）

```powershell
cd frontend
npm install
npm run dev
# 默认 http://localhost:5173（占用时递增）；/api、/ws 代理到后端 PORT
```

### 生产

```powershell
cd frontend && npm run build
python -m src.main
# 访问 http://{HOST}:{PORT}，静态资源由 FastAPI 托管 frontend/dist
```

### E2E 快速验证

```powershell
python e2e_test.py
# 默认连 http://127.0.0.1:8001，覆盖 T-01~T-09 与 T-10b
```

详细步骤见 `docs/测试需求.md`。

---

## API 与 WebSocket 要点

### REST（前缀 `/api`）

| 路径 | 说明 |
|------|------|
| `POST /resume/upload` | PDF 上传 → 画像 + 题目 |
| `GET /resume/profile` | 候选人画像 + 题目（含 DB 恢复） |
| `GET/PUT /interview/questions` | 读写当前会话题目（需活跃会话） |
| `POST /interview/start` | 切换 interview Agent，启动音频 |
| `POST /interview/stop` | 切换 eval Agent |
| `GET /interview/eval` | 生成/获取评价报告 |
| `POST /interview/suggest` | HTTP 触发追问（备用） |
| `GET /session/current` | 当前会话快照 |
| `GET /candidates` | 候选人列表 |
| `GET /candidates/{id}/history` | 历史面试 |

### WebSocket `ws://{host}/ws/interview`

**服务端推送**：`session_snapshot`、`transcript`、`suggestion` / `suggestion_delta` / `suggestion_final`、`status`、`error`、`heartbeat`

**客户端发送**：

| type | 说明 |
|------|------|
| `manual_input` | `{ source: "candidate"\|"interviewer", text }`；候选人输入后自动归档轮次 |
| `request_suggestion` | 手动触发追问 |
| `set_trigger_mode` | `{ mode: "auto"\|"manual" }` |
| `switch_agent` | `{ target_agent }` |
| `heartbeat` | 保活 |

前端在 `App.vue` `onMounted` 时 `store.connect()`，Pinia store 见 `frontend/src/stores/interview.js`。

---

## 关键约定

1. **不引入 LangGraph / AutoGen**：Agent 框架完全自建。
2. **Protocol 抽象**：`AudioCapturer`、`STTEngine` 可替换；非 Windows 自动降级 Mock。
3. **单进程 asyncio**：IO 均为 async；音频帧回调用 `run_coroutine_threadsafe` 桥接。
4. **层间隔离**：下层不感知上层；同层通过 `InterviewSession` 或 WS 消息通信。
5. **配置**：敏感项与运行参数放 `.env`；`config.py` 为唯一 Settings 定义（当前无 `config.yaml`）。
6. **音频启动失败不阻断面试**：`Orchestrator.switch_agent("interview")` 捕获音频异常后继续；手动输入仍可用（需 `TranscriptionManager` 已创建，通常 interview 启动成功时可用）。

---

## 代码规范约束

### 文件大小
- 每个 Python 源文件不超过 **1000 行**（不含空行和注释）；超过时拆分为子模块。
- 每个 Vue 单文件组件（`.vue`）不超过 **1000 行**；逻辑复杂时提取 composable。
- 每个非入口 Python 文件（非 `main.py`）不超过 **1000 行**（含注释）。

### 函数 / 方法
- 单个函数 / 方法不超过 **100 行**；超过时提取辅助函数。
- 函数参数不超过 **5 个**；更多参数改用 dataclass / TypedDict 封装。

### 模块与导入
- 禁止循环导入；依赖方向严格遵循分层规则（基础设施 → 框架 → Agent → Web）。
- 同一模块内禁止 `from x import *`；所有导入须显式列出符号。

### 类型标注
- 所有 Python 函数签名须有完整类型标注（参数 + 返回值）。
- 禁止裸 `Any`；如必须使用须在注释中说明原因。

### 异步规范
- 凡涉及 IO（网络、文件、数据库）的函数一律 `async def`。
- 不得在 async 上下文中调用阻塞 IO（如 `open()`、`requests.get()`）；文件 IO 用 `aiofiles`，HTTP 用 `httpx`。
- `asyncio.sleep(0)` 仅用于主动让出控制权，不得用于替代真实等待。

### 错误处理
- 不得使用裸 `except:`；至少捕获 `Exception` 并记录日志。
- 业务异常定义在 `src/models/exceptions.py`，不得直接抛出内建异常（`ValueError` 等）作为业务错误。

### 日志
- 统一使用标准库 `logging`；禁止在生产代码中使用 `print()`。
- 日志级别规范：调试信息用 `DEBUG`，正常流程用 `INFO`，可恢复异常用 `WARNING`，需人工介入用 `ERROR`。

### 测试
- 新增核心逻辑须附带单元测试，测试文件放 `tests/` 并与源码目录结构对应。
- 集成/E2E 参考 `docs/测试需求.md` 与根目录 `e2e_test.py`。

---

## 开发提示

- 方案调研若访问境外资源较慢，可执行 `clashctl on` 开启代理，`clashctl off` 关闭。
- 各子模块补充说明见 `src/*/CLAUDE.md`；详细设计见 `docs/arc/`。
- **勿将 API Key、Git Token 等密钥写入本文件或提交到仓库**；仅使用 `.env`（已 gitignore）。
