## Context

当前项目使用 Vue 3 + Vite SPA 作为前端，通过 FastAPI 在生产环境托管 `frontend/dist`。前端分四个路由页（Home / Prepare / Console / Report），构建需要独立的 Node.js 工具链。后端为 Python asyncio 单进程（FastAPI + uvicorn），两者通过 REST + WebSocket 通信。

本次重构目标：用 NiceGUI 替代 Vue 3，将前端实现迁移到 Python，并简化交互模型为单一 Agent 对话界面。

## Goals / Non-Goals

**Goals:**
- 消除 Node.js 工具链，前后端统一 Python
- 单页 Agent 对话界面，覆盖原四页功能
- NiceGUI 直接挂载到现有 FastAPI 实例，共享端口和进程
- 后端 API / WebSocket 接口保持不变
- 自然语言指令通过 LLM function calling 路由，不做关键词匹配

**Non-Goals:**
- 多用户、多会话隔离（本地单用户工具，不在本次范围）
- 移动端适配
- 离线 / PWA 支持
- 后端业务逻辑调整（agents、websocket、storage 等保持不动）

## Decisions

### D1：使用 NiceGUI 而非 Gradio / Streamlit

**选择 NiceGUI**。理由：
- `ui.run_with(fastapi_app)` 直接接管现有 FastAPI 实例，不引入额外进程
- 原生 `ui.chat_message`、`ui.upload`、`ui.markdown` 满足核心 UI 需求
- `ui.timer` + `asyncio` 支持 WebSocket 流式渲染，控制颗粒度足够
- Gradio 的 WebSocket 控制能力弱；Streamlit 不适合双向实时推送

### D2：NiceGUI 挂载方式 — `ui.run_with` + `app.on_startup`

**方案**：
1. `main.py` 中所有异步初始化提取为 `async def startup()`，注册到 `app.add_event_handler("startup", startup)`
2. 删除 `uvicorn.Config / uvicorn.Server / await server.serve()` 调用
3. 改为同步调用 `ui.run_with(app, host=..., port=...)`

**备选**：在 lifespan contextmanager 中初始化。选择 `on_startup` 是因为 `ui.run_with` 文档明确支持此模式，且对现有代码改动最小。

### D3：意图识别 — LLM function calling + 结构化工具

**方案**：新增 `src/tools/interview_control_tools.py`，每个意图对应签名明确的 `async def`，注册到现有 `ToolRegistry`。UI Agent 循环：用户输入 → `LLMClient.chat(tools=...)` → `tool_call` → `ToolRegistry.dispatch()` → 执行 → 回复消息。

工具函数内部通过 `httpx.AsyncClient` 调用本地 REST 接口（保持 HTTP 边界，与后端解耦）。

**放弃 bash/curl 方案**：安全风险高，且 `src/tools/` 结构化工具是项目既有模式，复用成本最低。

### D4：WebSocket 接收 — 后台 asyncio task + ui.timer 刷新

NiceGUI 页面通过 `websockets` 库在后台 task 中接收服务端推送，将消息写入 Python 队列；`ui.timer(interval=0.1)` 轮询队列并更新 UI 组件。这避免了在 NiceGUI 的同步回调中直接 `await` WS 消息的复杂度。

### D5：`CandidateProfile` 字段扩展 + DB migration

新增 `age: int | None = None` 和 `resume_markdown_path: str | None = None`。这两个字段均可空，对已有数据兼容（旧记录为 NULL）。SQLite 通过 `ALTER TABLE ... ADD COLUMN` 迁移，在 `database.py` `initialize()` 中执行（幂等）。

### D6：简历持久化

`POST /api/resume/upload` 改为：
1. 保存 PDF 到 `resumes/<session_id>_<timestamp>.pdf`
2. 将 `resume_text` 转换为 Markdown，保存到 `resumes/<session_id>.md`
3. 将绝对路径赋值给 `candidate.resume_markdown_path`

删除原有 `tempfile` + `os.unlink` 逻辑。

## Risks / Trade-offs

- **NiceGUI 版本锁定** → 记录当前版本到 `requirements.txt`，避免 API 破坏性升级
- **ui.timer 轮询延迟** → 间隔设为 100ms，流式建议感知延迟可接受；若需更低延迟可改为 `ui.update` + asyncio event
- **httpx 内部回环调用** → 工具函数通过 `http://127.0.0.1:<port>` 调用自身 API；需确保 uvicorn 已就绪再接受请求（`startup` 事件保证）
- **DB migration 无回滚** → 新增字段为 nullable，不影响已有行；若回滚只需删除列（SQLite 需重建表，可接受）
- **LLM function calling 误触发** → 工具描述应精确；默认将无法识别的输入作为普通对话回复，不执行工具

## Migration Plan

1. 安装 `nicegui` 和 `httpx`（`pip install nicegui httpx`）
2. 创建 `resumes/` 目录（`.gitkeep`）
3. 实现后端最小改动（model 字段、routes、prompt_builder、main.py 启动逻辑）
4. 实现 `src/web/ui.py`（布局骨架 → WS 接入 → 完整功能）
5. 实现 `src/tools/interview_control_tools.py`
6. 删除 `frontend/` 目录及 `src/web/app.py` 中的 SPA fallback 路由
7. 端到端验收（按需求文档验收标准逐条测试）

## Open Questions

- NiceGUI 页面状态是否需要支持多标签页（同一本地用户在不同浏览器 tab 打开）？当前方案为单实例，多 tab 共享同一状态。
- `resumes/` 目录是否需要定期清理旧文件？当前方案不清理，本地磁盘空间可接受。
