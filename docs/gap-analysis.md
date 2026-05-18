# 端到端测试与缺口分析

**测试日期：** 2026-05-18  
**服务地址：** http://127.0.0.1:8001  
**测试简历：** `temp/祝家乐.pdf`  

---

## 一、服务启动

服务已在端口 8001 上运行（由 `scripts/start-dev.ps1` 预先启动）。使用 venv Python 重新启动测试启动流程，观察到以下结果：

- 日志配置、数据库初始化、所有路由挂载均无报错
- 仅有一条 WARNING（见下方日志分析）
- HTTP 200 / `{"session":null}` 确认服务健康

**结论：** 启动流程正常。

---

## 二、功能测试结果

> **说明**：浏览器自动化工具（Playwright）因安全限制无法直接触发文件选择对话框（"File chooser can only be shown with user activation"），PDF 上传通过 Python httpx 直接调用 API 完成；其余交互通过 WebSocket 脚本和 REST API 验证。

| 功能 | 状态 | 测试方式 | 说明 |
|------|------|---------|------|
| 页面加载 | ✅ 正常 | 浏览器 | 所有预期元素（开始/结束按钮、三标签面板、上传按钮）可见 |
| PDF 上传按钮（UI） | ✅ 存在 | 浏览器截图 | 按钮位于底部工具栏；真实浏览器中可正常点击 |
| PDF 上传（API） | ✅ 正常 | Python httpx | 解析成功，生成 10 道题目；但耗时 ~3.5 分钟 |
| 简历解析（LLM） | ✅ 正常 | 日志 | `parse_resume` 工具调用成功，候选人信息正确提取 |
| 面试题目生成 | ✅ 正常 | 日志 + API | 10 道题目，按维度分类，但 LLM 调用耗时 ~172 秒 |
| 开始面试 | ✅ 正常 | REST API | stage 正确切换为 `interviewing` |
| 手动转写输入（WS） | ✅ 正常 | WebSocket 脚本 | 消息接收 → `transcript` 事件回传 → 轮次自动归档 |
| 自动追问建议 | ✅ 正常 | WebSocket 脚本 | 候选人消息后自动触发，LLM 约 1.8 秒完成，WS 流式推送 |
| 结束面试 | ✅ 正常 | REST API | stage 正确切换为 `evaluating` |
| 评价报告生成 | ✅ 正常 | REST API | 约 12 秒，整体评分 3.0/10，recommendation = no_hire，3 个维度 |
| 报告展示（UI） | ✅ 正常 | 代码审查 | `_render_report` 函数正确读取 `dimension` 字段 |
| 验证错误处理 | ✅ 正常 | 浏览器 | 未上传简历时点击"开始面试"，正确显示"你还未上传简历"错误提示 |

---

## 三、日志分析

**`logs/app.log`**

- 仅 1 条 WARNING：`python_multipart.multipart: Expected boundary character 45, got 52 at index 2`
  - 原因：第一次 API 调用时 multipart boundary 格式错误（来自 PowerShell `Invoke-RestMethod` 构造的不规范请求）
  - 影响：该请求被拒绝（HTTP 400），正常的 curl/httpx/浏览器上传不受影响
- 无 ERROR 级别日志
- LLM 调用均成功，token 使用正常

**`logs/backend.out.log`**

- 记录 uvicorn 访问日志，无异常请求

**`logs/backend.err.log`**

- 内容与 `app.log` 一致（同一日志流），仅含上述 1 条 WARNING

**`logs/app.error.log`**

- 文件为空（0 字节）— 无 ERROR 级别事件

---

## 四、已识别缺口

### 4.1 已实现但存在缺陷

| 编号 | 缺陷 | 位置 | 严重程度 |
|------|------|------|---------|
| B1 | 用户消息提示调用 `parse_resume_pdf`，但工具注册名为 `parse_resume` | `src/agents/resume_agent.py:48` | 低（当前 LLM 仍使用正确名称，但会误导） |
| B2 | 题目生成 LLM 调用耗时约 172 秒，整个上传流程约 3.5 分钟，无进度反馈 | `src/agents/resume_agent.py:_generate_questions` | 高（严重影响用户体验） |
| B3 | 追问建议仅通过 WS 流式推送，未写回 `ConversationRound.llm_suggestion`，无法在历史记录中复查 | `src/agents/interview_agent.py:generate_suggestion` | 中 |
| B4 | `save_interview` 传入空字符串作为录音路径，数据库中全录路径字段始终为空 | `src/storage/memory_module.py:save_interview` | 低（功能不影响，但数据不完整） |
| B5 | `regenerate_questions` 工具仅 GET 已存数据（`/api/resume/profile`），未触发 LLM 重新生成 | `src/tools/interview_control_tools.py:76-91` | 中（名实不符，功能误导） |

### 4.2 完全缺失的功能

| 编号 | 功能 | 影响 |
|------|------|------|
| M1 | **实时音频采集（WASAPI）**：`WasapiCapturer` 返回静音 PCM，无实际麦克风/扬声器采集 | 核心功能缺失，转写只能靠手动输入 |
| M2 | **实时 STT（百度 ASR）**：`BaiduRealtimeSTT` 是空 stub，不返回任何转写结果 | 核心功能缺失，与 M1 共同导致"实时转写"实质不可用 |
| M3 | **候选人选择器 UI**：`GET /api/candidates` 接口存在，但 `ui.py` 从未调用，无法选择历史候选人 | 每次面试必须重新上传 PDF，历史数据无法复用 |
| M4 | **岗位要求记忆（USER.md）**：CLAUDE.md 提及，但代码中无任何实现路径 | 题目生成和追问建议缺少岗位上下文 |
| M5 | **追问建议手动触发按钮**：`POST /api/interview/suggest` 已实现，但 UI 无专用按钮（仅能通过聊天框下自然语言指令间接触发） | 用户无法直观控制 |

---

## 五、实现方案建议

> 以下方案按优先级从高到低排列，建议逐项与产品方向确认后再开始实施。

---

### P1（高优先级）— 实时音频与 STT

**M1 + M2：实现真实 WASAPI 采集 + 百度 STT**

这是本工具的核心差异化功能，不实现则"实时转写"形同虚设，面试官只能手动键入，体验大打折扣。

**WASAPI 采集（`src/audio/wasapi.py`）：**
- 使用 `pyaudio` 或 Windows 原生 `sounddevice`（支持 WASAPI loopback 模式）
- Loopback 设备：采集扬声器输出（候选人声音）
- 麦克风设备：采集面试官声音
- 两路独立 `AudioFrame` 推入 `AudioStreamBridge`

**百度实时 ASR（`src/audio/baidu_stt.py`）：**
- 接入百度语音识别实时 API（WebSocket 接口 `wss://vop.baidu.com/realtime_asr`）
- 需配置 `BAIDU_APP_ID`、`BAIDU_API_KEY`、`BAIDU_SECRET_KEY`（加入 `.env`）
- 发送 PCM 16k/16bit 音频帧，接收实时 + 最终识别结果

**工作量估计：** 3-5 天（含测试，需要有麦克风和扬声器的 Windows 环境）

---

### P2（高优先级）— 候选人选择器 UI

**M3：在 UI 中加入候选人管理入口**

当前每次面试需重新上传 PDF，历史候选人数据无法复用。建议在上传区域旁加入"选择已有候选人"下拉框。

**实现要点：**
1. 页面加载时调用 `GET /api/candidates` 获取候选人列表
2. 选中候选人后调用 `GET /api/candidates/{id}/history` 获取最新题目计划
3. 跳过上传流程，直接进入面试准备阶段
4. 可选：显示该候选人历次面试记录摘要

**工作量估计：** 1-2 天

---

### P3（中优先级）— 上传进度反馈

**B2：题目生成时间过长（约 3.5 分钟），无任何进度指示**

**方案 A（推荐）：将解析和题目生成拆分为异步流程**
- 上传接口仅完成 PDF 存储 + 文本提取，立即返回 `candidate_id`
- 通过 WebSocket 推送 `resume_status` 事件（`parsing` → `generating_questions` → `ready`）
- UI 显示进度条或状态提示

**方案 B（快速）：保持同步，但添加 SSE 或 WS 进度推送**
- 在 resume_agent 各阶段间发送 WS 进度消息
- 工作量较小，但仍需等待完成才能开始面试

**工作量估计：** 方案 A 2-3 天；方案 B 半天

---

### P4（中优先级）— 真正的题目重新生成

**B5：`regenerate_questions` 应触发 LLM 重新生成，而非仅重载 DB 数据**

**实现：**
- 新增 `POST /api/resume/questions/regenerate` 接口
- 接受可选的 `job_description` 参数
- 调用 `ResumeAgent.generate_questions`，将新题目写回 session 和 DB
- `interview_control_tools.py` 中更新 `regenerate_questions` 工具调用该新接口

**工作量估计：** 半天

---

### P5（中优先级）— 岗位要求集成

**M4：在 `USER.md` 中持久化岗位要求，并注入题目/追问生成**

**实现：**
- 在 `MemoryModule` 中添加 `get_job_requirements()` / `save_job_requirements()` 方法（读写 `USER.md` 或 DB 中的一条特殊记录）
- 在 UI 聊天框中支持自然语言设置岗位要求（面试官说"这次招 Python 高级工程师，要求熟悉分布式系统"）
- `PromptBuilder` 在题目生成和追问建议时注入岗位要求

**工作量估计：** 1-2 天

---

### P6（低优先级）— 追问建议持久化

**B3：将追问建议写回 `ConversationRound.llm_suggestion`**

- 在 `InterviewAgent.generate_suggestion` 结束时，查找当前最新 round，写入 `llm_suggestion` 字段
- 评价报告生成时可引用历次追问建议，提升 eval 质量

**工作量估计：** 半天

---

### P7（低优先级）— 小缺陷修复

| 缺陷 | 修复方式 | 工作量 |
|------|---------|--------|
| B1 工具名称注释不符 | 将 `resume_agent.py:48` 的 `parse_resume_pdf` 改为 `parse_resume` | 5 分钟 |
| B4 录音路径未写入 DB | `save_interview` 从 `AudioRecorder.get_result()` 获取路径并传入 | 1 小时 |
| M5 追问手动触发按钮 | 在转写 Tab 或工具栏添加"触发追问"按钮，调用 `POST /api/interview/suggest` | 半天 |

---

## 六、优先级确认清单

以下事项请与您确认后再开始实施：

1. **实时音频（P1）**：是否现在就开始实现 WASAPI + 百度 STT？需要配置百度 API key。
2. **候选人选择器（P2）**：是否在下次迭代加入？
3. **上传进度（P3）**：方案 A（重构为异步）还是方案 B（快速进度推送）？
4. **岗位要求（P5）**：是否纳入本期范围？还是先保持题目仅基于简历生成？
