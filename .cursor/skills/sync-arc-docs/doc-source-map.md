# docs/arc ↔ 源码对照

修订时优先读下列源码；表外路径仅在文档已引用或 grep 发现现行入口时再读。

配置统一入口：`src/config.py`（`get_settings()`，从 `.env` / 环境变量加载）。本项目**无**独立 `config/*.yaml` 业务配置树作为 arc 同步主源。

| 文档 | 优先阅读源码 |
|------|-------------|
| `overview.md` | `src/main.py`、`src/config.py`、`src/web/app.py`、`src/web/ui.py`；目录树对照 `src/` 各子包 |
| `agents.md` | `src/agents/`（`main_agent.py`、`interview_controller.py`、`resume_agent.py`、`interview_agent.py`、`eval_agent.py`、`base.py`、`prompts.py`）、`src/models/session.py` |
| `api.md` | `src/web/routes.py`、`src/web/websocket.py`、`src/web/schemas.py`、`src/web/app.py` |
| `flows.md` | 同上 + `src/agents/interview_controller.py`、`src/audio/manager.py`、`src/audio/transcription.py`、`src/audio/trigger.py`、`src/tools/dispatch_to_agent.py` |
| `storage.md` | `src/storage/`（`memory_module.py`、`candidate_store.py`、`interview_store.py`、`eval_store.py`、`user_memory.py`、`conversation_logger.py`）、`src/config.py`（`CANDIDATES_DIR` / `RECORDINGS_DIR`） |
| `prompt-assembly.md` | `src/agents/main_agent.py`、`src/framework/prompt_builder.py`、`src/agents/prompts.py`、`src/agents/eval_agent.py`、`src/framework/skill.py` |
| `context-memory.md` | `src/framework/context.py`、`src/storage/user_memory.py`、`src/storage/memory_module.py`、`src/config.py`（`CONTEXT_*`） |
| `llm-providers.md` | `src/llm/providers.py`、`src/llm/client.py`、`src/llm/config.py`、`src/llm/protocol.py`、`src/config.py`（`LLM_*`） |
| `CLAUDE.md`（架构文档表） | 仅当 `docs/arc/` 文件增删或表内职责描述过时时更新；非每篇必改 |

## 现行主链路核对点（易过时）

修订总览 / 流程 / Agent 文档时务必对照源码确认，勿照搬旧文档：

- **启动组装**：`src/main.py` 的 `lifespan()` 手动组装依赖（非自动 DI 容器）
- **对话入口**：用户聊天 → `POST /api/chat` → `MainAgent.handle_chat`；专项任务经 `dispatch_to_agent`（当前主要 `agent="resume"`）
- **面试状态机**：`InterviewController` 阶段迁移；开始/结束面试与 Audio / Eval 的衔接以 controller 为准
- **实时链路**：Audio 采集 → STT（`STT_ENGINE`：baidu / xunfei / volc；非 Windows 多为 Mock）→ 转写 → `SuggestionTrigger` → `InterviewAgent` 追问建议 → WebSocket 推送
- **存储后端**：`candidates/` 文件系统（Markdown + YAML frontmatter），**不是** SQLite；录音在 `recordings/`
- **记忆分层**：面试上下文 `ContextManager`；面试官偏好 `UserMemoryStore` + `USER.md`；候选人长期记忆 `MemoryModule` + `candidates/`
- **LLM**：OpenAI 兼容客户端 + `ProviderProfile`（`src/llm/providers.py`）；换平台改 `LLM_PROVIDER` / `.env`，勿在文档写死单一厂商分支逻辑

## 相关但通常不单独成篇的源码

以下模块多在上述文档中顺带出现；除非用户点名，否则不必新开 arc 文档：

- `src/tools/` — 在 `agents.md` / `prompt-assembly.md` / `flows.md` 中按调用关系提及
- `src/audio/` — 在 `overview.md` / `flows.md` / `agents.md`（InterviewAgent）中提及
- `src/web/ui.py` — NiceGUI 纯 UI；细节以 API/WS 契约为准，避免把前端布局写进 arc
