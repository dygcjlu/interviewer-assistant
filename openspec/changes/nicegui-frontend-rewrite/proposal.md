## Why

当前前端为 Vue 3 SPA，引入了独立的 Node.js 工具链和四个路由页，但本项目是本地单用户工具，这些复杂度并不必要。用 Python（NiceGUI）重写前端可消除双语言栈，降低维护成本，同时将交互模型简化为单一 Agent 对话界面，与 AI 驱动的面试辅助定位更契合。

## What Changes

- **BREAKING** 删除整个 `frontend/`（Vue 3 + Vite + Node.js 工具链）
- 新增 `src/web/ui.py`：NiceGUI 单页界面，挂载到现有 FastAPI 实例（`ui.run_with`）
- 新增 `src/tools/interview_control_tools.py`：UI Agent 工具函数，通过 LLM function calling 实现自然语言意图识别（开始/结束面试、触发追问、获取报告等）
- 修改 `src/main.py`：将启动逻辑从 `uvicorn.Server` 改为 `ui.run_with`，异步初始化提取为 `startup()` 事件处理器
- 修改 `src/web/app.py`：删除 SPA fallback 路由（`/{full_path:path}`），根路径交由 NiceGUI 接管
- 修改 `src/web/routes.py`：PDF 从临时文件改为持久化保存到 `resumes/` 目录，同时生成 Markdown 副本
- 修改 `src/models/candidate.py`：`CandidateProfile` 新增 `age` 和 `resume_markdown_path` 字段
- 修改 `src/framework/prompt_builder.py`：system prompt 固定区注入候选人摘要（name、age、education、skills、resume_summary）

## Capabilities

### New Capabilities

- `nicegui-ui`: 单页 Agent 对话界面——顶部状态栏、主对话区（聊天气泡 + 流式建议渲染）、右侧面板（转写/题目/报告三 Tab），由 NiceGUI 实现并挂载到 FastAPI
- `ui-agent-intent`: 对话框自然语言指令经 LLM function calling 路由到 `interview_control_tools.py` 中的结构化工具函数（start_interview、stop_interview、get_eval_report、request_suggestion、regenerate_questions）
- `resume-persistence`: 简历上传时 PDF 持久化保存到 `resumes/`，`resume_text` 同步转换并保存为 Markdown，路径写入 `CandidateProfile.resume_markdown_path`

### Modified Capabilities

- `candidate-profile`: `CandidateProfile` 新增 `age: int | None` 和 `resume_markdown_path: str | None` 字段，影响数据模型与 DB schema
- `prompt-building`: PromptBuilder system prompt 固定区新增候选人摘要注入，改变 InterviewAgent / EvalAgent 的上下文结构

## Impact

- **删除依赖**：Node.js、npm、Vue 3、Vite、Pinia、Vue Router
- **新增依赖**：`nicegui`、`httpx`（用于工具函数内部调用本地 REST）
- **API 不变**：所有 `/api/*` 和 `/ws/interview` 端点保持不变，NiceGUI 作为新的消费方
- **数据库**：`CandidateProfile` 字段变更可能需要 migration（`age`、`resume_markdown_path`）
- **目录**：新增 `resumes/` 目录用于存储 PDF 和 Markdown 简历文件
