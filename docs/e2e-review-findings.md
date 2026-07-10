# 端到端浏览器测试 · 发现问题与优化点清单

> 测试日期：2026-07-07
> 测试方式：以 `MOCK_AUDIO=true` 启动服务（`.venv`，http://127.0.0.1:8088），使用浏览器以真实用户身份走完全部核心流程：
> 首页浏览 → 上传简历 → 解析 → 岗位要求对话 → 生成简报/问题清单 → 开始面试（Mock 音频） → 实时转写/追问建议 → 结束面试 → 评价报告 → PDF 导出 → 重复上传去重 → 候选人对比/删除。
> 定位：本清单只**记录**问题，不做修改。与 `docs/opensource-optimization-plan.md` 互补——该计划覆盖工程规范/测试/重构，本清单聚焦 E2E 实测发现的**功能缺陷与 UI/UX 问题**。

---

## A. 功能缺陷（实测复现，按严重度排序）

### A-1 候选人「横向对比」功能完全不可用 【P0 · Bug】

- **现象**：勾选 2 名候选人复选框后，"已选 N 人 / 横向对比"工具栏**从不出现**，对比功能无法触达。
- **根因**：`src/web/ui.py:1390` 用 `cb.on("update:model-value", _on_cb_change)` 注册事件，回调里读 `e.value`（`ui.py:1384`），但 NiceGUI 泛型事件参数是 `GenericEventArguments`（只有 `e.args`），**每次勾选后端都抛 `AttributeError: 'GenericEventArguments' object has no attribute 'value'`**（`logs/backend.err.log` 大量堆栈），`selected_for_compare` 永远为空。
- **修复方向**：改用 `ui.checkbox(..., on_change=...)` 或 `on_value_change`，读 `e.value`。`.cursor/rules/browser-testing.mdc` 已记录过同类坑（select 组件），属同一模式的回归。

### A-2 删除候选人功能不可用 【P0 · Bug】

- **现象**：点击候选人行的删除按钮，没有确认弹窗，候选人未被删除；副作用是该候选人反而被**选中**（右侧面板加载其简历）。
- **根因**（两处叠加，`src/web/ui.py`）：
  1. `_confirm_delete_dialog`（`ui.py:1572`）在 `asyncio.create_task` 的后台任务里创建 `ui.dialog()`，NiceGUI 抛 `RuntimeError: The current slot cannot be determined ... you try to create UI from a background task`（`backend.err.log` 可见），弹窗永远不出现，删除流程在 `await _confirm_delete_dialog` 处中断。
  2. `delete_btn.on("click", ...)`（`ui.py:1483`）未阻止事件冒泡（对比：复选框有 `stop_propagation`，`ui.py:1391-1396`），点击删除会同时触发行点击 → 选中候选人。
- **修复方向**：弹窗在页面上下文中预创建或用 `with row_el:` 进入 slot；删除按钮补 `.on('click.stop', ...)`。

### A-3 结构化问题清单的自动覆盖检测无效 【P1 · Bug/质量】

- **现象**：面试进行 5 轮，对话明确覆盖了清单第 1 题（订单中心架构/微服务拆分）、Redis 缓存等主题，结束后覆盖进度仍为 **0/10**。日志确认 `POST /api/interview/questions/check-coverage` 每轮均被调用且返回 200，但没有任何题目被标记。
- **疑点**：
  1. `routes.py:721` 的判定 prompt 每次只给"最新一轮"文本，mock 对话每轮信息量小，LLM（temperature 0.1）判定过于保守；
  2. 服务端 `_auto_check_coverage`（`routes.py:663`）只在 `dispatch_to_agent` 返回 `suggestion` 时触发（`src/tools/dispatch_to_agent.py:116-127`），实时面试的追问建议不走该路径，形同虚设；
  3. 面试结束后 `GET /api/interview/last-round` 返回空 `round_text`，最后一轮永远检测不到。
- **修复方向**：覆盖判定改为携带近 N 轮上下文；prompt 放宽"部分覆盖也标记"；给判定结果加日志便于观测；评估删除 `_auto_check_coverage` 死代码或接到正确的触发点。

### A-4 「问题」Tab 与后端数据不同步 【P1 · Bug】

- **现象**：问题清单已生成（API `GET /api/interview/questions` 可查到 10 题），但「问题」Tab 显示"暂无问题清单"，甚至（切换候选人后）**完全空白无任何文案**。
- **根因**：`_on_candidate_select_inner`（`ui.py:1491`）只渲染 profile/brief/report，**不加载问题清单**；问题面板只在 `_sync_candidate_panel`（聊天完成回调）里刷新，且依赖 `state["candidate_id"]` 已被正确设置。
- **修复方向**：候选人选中时同步拉取并渲染问题清单；问题面板空态文案兜底。

### A-5 会话候选人与 UI 状态不同步 【P1 · UX】

- **现象**：通过对话流程绑定/切换候选人后（如解析完成、简报生成完成），顶栏候选人名一直显示"—"，左侧列表不高亮当前候选人，右侧面板不刷新；agent 明明说"可在「简报」Tab 查看"，用户点开却是"暂无面试简报"。必须手动点击左侧列表中的候选人行才能对齐。
- **修复方向**：session 候选人变化时（SSE/WS 事件或轮询 `/api/session/current`）驱动顶栏、列表高亮、右侧面板统一刷新；新解析完成的候选人自动滚动到可视区并选中。

### A-6 按钮可用态未与状态机联动 【P2 · UX】

- 空闲状态下"结束面试"按钮可点击（红色高亮，未禁用）；面试中"开始面试"也未禁用。应随 `stage` 禁用不适用的按钮，避免误操作。

---

## B. UI / 排版 / 美观

### B-1 聊天气泡不渲染 Markdown 【P1 · 高影响】

Agent 回复充满 `**加粗**`、`## 标题`、`| 表格 |` 原文符号直接显示（`_bubble` 用 `ui.label` + pre-wrap，`ui.py:743`）。简历分析、简报确认等核心输出可读性差，是**演示观感最大的减分项**。建议 Agent 气泡改用 `ui.markdown`（用户气泡保持纯文本）。

### B-2 空状态缺失，新用户无引导 【P1】

- 首次打开：聊天区纯空白，无欢迎语/操作引导（"上传简历开始 →"）；
- 候选人列表为空时无提示；
- 右侧 Tab：转写/问题/报告无数据时是**纯白面板**（简报 Tab 有文案，其余没有）。
- 建议：统一空态组件（图标+一句话引导），聊天区加欢迎消息。

### B-3 视觉设计整体偏"原型感" 【P2】

- 顶栏无产品名/Logo，只有状态徽章+轮次，品牌感缺失；
- 整体灰白配色单一，卡片、面板层次感弱；
- 根布局在 900px 视口下垂直溢出约 32px，出现整页滚动条（`document.scrollHeight=932 vs clientH=900`）；
- 作为开源项目的第一印象界面，建议做一轮轻量视觉打磨（配色 token、标题栏、间距系统）。

### B-4 右侧面板 Markdown 排版失衡 【P2】

简历详情/简报渲染时 h1/h2 字号过大（"李明哲""教育背景"渲染成巨型标题），与窄面板不协调。建议对面板内 markdown 容器做 typography 缩放（如 `prose-sm` 风格）。

### B-5 评价报告显示英文枚举值 【P2】

"建议：hire" 直接露出后端枚举，应映射为中文（推荐录用/待定/不推荐）并配色徽章。

### B-6 追问建议文本未清洗 【P2】

实测有建议卡片以孤立引号 `"` 开头、正文出现individual乱码字符（如"你提到了R你们项目…"）。建议对 LLM 输出做首尾引号/空白清洗，排查流式拼接是否丢字。

### B-7 候选人列表可用性 【P2】

- 16+ 人无搜索/筛选/排序，找人靠滚动；
- 新增候选人不在列表顶部（似按创建序），也不自动选中；
- 历史遗留重复数据（两个 Wei Chen）暴露早期无去重的问题，建议提供数据清理入口或提示。

---

## C. 产品 / 流程

### C-1 明确指令下 Agent 仍反复确认 【P2】

用户已说"请生成面试简报"，Agent 仍回问"您还有其他想重点考察的方向吗？"，多一轮往返。建议 MainAgent prompt 增加约束：面试官已明确下达生成指令时直接执行，不再追问。

### C-2 Mock 演示数据不自洽 【P2 · 影响 Demo】

`data/mock_script.json` 的候选人是"张伟/5年/Python"，与演示简历（李明哲/8年/Java）不匹配，评价报告可信度受影响。录制 README Demo 前建议配一套对齐的"样例简历 + mock 对话脚本"。

### C-3 上传后不解析则候选人"失踪" 【P2】

上传 PDF 后需手动点"解析简历"按钮；若用户不点，文件已保存但候选人不出现在列表，无任何后续提醒。建议上传完成气泡中加提示或支持自动解析选项。

---

## D. 工程 / 运维

### D-1 端口冲突时启动日志误导 【P2】

端口被占用时日志顺序为：`ready on http://...` → `[Errno 10048] bind 失败` → 退出。"ready" 先于实际 bind 打印；且 `start-dev.ps1` 的健康检查会命中**旧进程**误判启动成功（本次测试实际踩到：8088 被 17:30 的 conda 进程占用）。建议：bind 成功后再打 ready；start 脚本校验端口归属 PID。

### D-2 LLM 请求全量入日志 【P2 · 隐私/体积】

`src/llm/client.py` 每次请求以 INFO 级把完整 `messages_body`（含 system prompt 全文与**候选人简历内容**）写入 `app.log`，单日膨胀数 MB，且敏感信息落盘位置超出 README 数据隐私声明范围。建议降为 DEBUG/截断，或提供开关。

### D-3 日志/stderr 编码不统一 【P3】

Windows 下 `backend.err.log` 与 PowerShell 读取 `app.log` 中文均乱码（GBK/UTF-8 混用），排障不便。建议 logging handler 显式 `encoding="utf-8"`，进程启动加 `PYTHONIOENCODING=utf-8`。

### D-4 代码内遗留非中英文注释 【P3】

`src/web/ui.py:404` 存在韩语注释（`순수 Enter만 잡음`），开源前建议统一清理。

---

## E. 本次实测确认正常的部分（无需处理）

- 简历上传（20MB 限制/非 PDF 拦截）、Qwen-VL 解析、真实姓名去重弹窗（覆盖/保留两份/取消三选一）均正常工作；
- 面试全流程状态机正确：空闲→面试中→评价中→已完成，轮次计数、Mock 转写双声道展示、追问建议卡片实时出现；
- 评价报告生成、维度评分折叠面板、PDF 导出（`/report/export` 200）正常；
- Agent 工具调用可视化（`dispatch_to_agent`/`manage_user_memory` 折叠卡片）已实现且效果好；
- USER.md 面试官记忆生效（跨会话记住岗位要求，还能在岗位变更时主动提示匹配度差异）。

---

## 建议的修复优先级

| 批次 | 内容 |
|---|---|
| 第一批（Bug，先修） | A-1 对比勾选、A-2 删除候选人、A-4 问题 Tab 同步、B-1 聊天 Markdown 渲染 |
| 第二批（体验） | A-3 覆盖检测、A-5 状态同步、A-6 按钮联动、B-2 空状态、B-5 枚举中文化 |
| 第三批（打磨） | B-3 视觉、B-4 排版、B-6 文本清洗、B-7 列表可用性、C-1~C-3、D-1~D-4 |
