## 1. 环境与依赖

- [x] 1.1 安装 `nicegui` 和 `httpx`（`pip install nicegui httpx`），更新 `requirements.txt`
- [x] 1.2 创建 `resumes/` 目录，添加 `.gitkeep`，更新 `.gitignore` 忽略 `resumes/*.pdf` 和 `resumes/*.md`

## 2. 数据模型与 DB（后端最小改动）

- [x] 2.1 `src/models/candidate.py`：在 `CandidateProfile` 添加 `age: int | None = None` 和 `resume_markdown_path: str | None = None`
- [x] 2.2 `src/storage/database.py`：在 `initialize()` 中添加幂等的 `ALTER TABLE candidates ADD COLUMN` 迁移（age、resume_markdown_path）

## 3. 简历持久化（routes.py）

- [x] 3.1 `src/web/routes.py` — `upload_resume`：将 `tempfile.NamedTemporaryFile` 替换为持久化写入 `resumes/<session_id>_<timestamp>.pdf`，删除 `os.unlink` 逻辑
- [x] 3.2 PDF 解析完成后，将 `candidate.resume_text` 转换为 Markdown 并写入 `resumes/<session_id>.md`，赋值 `candidate.resume_markdown_path`

## 4. PromptBuilder 候选人摘要注入

- [x] 4.1 `src/framework/prompt_builder.py`：在 `build_system_prompt()` 固定区添加候选人摘要块（name、age、education、skills、resume_summary），仅在 session 有候选人时注入

## 5. 启动逻辑重构（main.py + app.py）

- [x] 5.1 `src/main.py`：将 `bootstrap()` 中的异步初始化提取为 `async def startup()`，通过 `app.add_event_handler("startup", startup)` 注册
- [x] 5.2 `src/main.py`：删除 `uvicorn.Config / uvicorn.Server / await server.serve()` 调用，改为在 `if __name__ == "__main__":` 中调用 `ui.run_with(app, host=..., port=...)`
- [x] 5.3 `src/web/app.py`：删除 SPA fallback 路由（`/{full_path:path}`），导入 `src.web.ui` 模块以触发 NiceGUI 页面注册

## 6. UI Agent 工具函数（interview_control_tools.py）

- [x] 6.1 新建 `src/tools/interview_control_tools.py`，实现五个工具函数：`start_interview`、`stop_interview`、`get_eval_report`、`request_suggestion`、`regenerate_questions`，每个使用 `httpx.AsyncClient` 调用本地 REST
- [x] 6.2 为每个工具函数添加完整类型标注和 JSON Schema docstring（供 LLM function calling）
- [x] 6.3 在 `ToolRegistry` 中注册上述工具函数

## 7. NiceGUI 页面骨架（ui.py）

- [x] 7.1 新建 `src/web/ui.py`，使用 `@ui.page("/")` 注册根路径页面
- [x] 7.2 实现顶部状态栏（面试阶段 badge、候选人姓名、当前轮次）
- [x] 7.3 实现主对话区布局（60% 宽，`ui.chat_message` 列表 + 滚动容器）
- [x] 7.4 实现右侧面板（40% 宽，三 Tab：转写 / 题目 / 报告）
- [x] 7.5 实现底部输入区（文本框 + PDF 上传按钮 + 发送按钮）

## 8. WebSocket 接入与实时渲染

- [x] 8.1 在 `ui.py` 页面启动时建立 WebSocket 连接（`ws://127.0.0.1:<port>/ws/interview`），在后台 asyncio task 中接收消息并写入队列
- [x] 8.2 使用 `ui.timer(interval=0.1)` 轮询队列，分发消息到各处理函数
- [x] 8.3 处理 `transcript`：追加到转写 Tab，并（可配置）在对话区插入轻量内联条目
- [x] 8.4 处理 `suggestion_delta`：流式更新当前建议气泡内容
- [x] 8.5 处理 `suggestion_final`：完成气泡，添加"采用"/"忽略"按钮；点击"采用"在对话区插入面试官气泡（纯 UI，不发送 WS 消息）
- [x] 8.6 处理 `session_snapshot`：更新顶部状态栏和题目 Tab
- [x] 8.7 处理 `status`：更新顶部连接/面试状态
- [x] 8.8 处理 `error`：在对话区插入红色错误气泡

## 9. UI Agent 循环接入

- [x] 9.1 发送按钮 / 回车触发 UI Agent 循环：将用户输入送入 `LLMClient.chat(tools=interview_control_tools)`
- [x] 9.2 解析 LLM 返回：若有 `tool_call` 则通过 `ToolRegistry.dispatch()` 执行，若为文本则直接插入 Agent 气泡
- [x] 9.3 PDF 上传成功后，将解析结果（候选人名、技能、问题列表）作为 Agent 气泡展示在对话区
- [x] 9.4 手动输入转写（转写 Tab）：来源选择 + 发送 → WS `manual_input` 消息
- [x] 9.5 确保"开始面试"/"结束面试"按钮与自然语言指令走相同的工具函数路径

## 10. 旧前端清理

- [x] 10.1 删除整个 `frontend/` 目录
- [x] 10.2 确认 `.gitignore` 中移除 `frontend/` 相关条目（如有），保留 `resumes/` 忽略规则

## 11. 端到端验收

- [ ] 11.1 启动后访问 `http://127.0.0.1:8000` 可见 Agent 对话界面 (需手动验证)
- [ ] 11.2 上传 PDF → Agent 对话区回复解析结果和问题列表 (需手动验证)
- [ ] 11.3 点击"开始面试"或输入指令 → 面试阶段切换，顶部 badge 更新 (需手动验证)
- [ ] 11.4 手动输入候选人文字 → 转写 Tab 更新，~2s 后对话区出现流式追问建议 (需手动验证)
- [ ] 11.5 点击"结束面试" → 报告 Tab 自动激活并展示评价报告 (需手动验证)
- [x] 11.6 确认全程无 `print()` 输出，日志仍走 `src/logging/` (no print() calls in any new code)
