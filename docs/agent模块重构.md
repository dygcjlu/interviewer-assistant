# Agent 模块重构方案

## 一、重构目标

突出"Agent 服务"特点，设立一个常驻的 **MainAgent** 作为面试官的对话入口，具备全局信息感知能力。删除运行在前端的 UI Agent，将对话逻辑全部收归后端。

---

## 二、架构变化对比

### 现有架构

```
前端 NiceGUI
    └── UI Agent（mini-agent，NiceGUI 进程内）
            ├── 候选人上下文注入
            └── 通过 HTTP 工具驱动后端

后端
    └── Orchestrator（状态机 + 路由）
            ├── ResumeAgent
            ├── InterviewAgent
            └── EvalAgent
```

### 重构后架构

```
前端 NiceGUI（纯 UI 层，无 Agent 逻辑）
    ├── 聊天框         → POST /api/chat          → MainAgent
    ├── 上传简历       → POST /api/resume/upload → 文件存储后通知聊天框
    ├── 候选人切换     → POST /api/candidate/select → 更新 MainAgent 上下文
    ├── 开始面试按钮   → POST /api/interview/start  → InterviewController
    └── 结束面试按钮   → POST /api/interview/stop   → InterviewController

后端
    ├── MainAgent（常驻单例，所有对话入口）
    ├── InterviewController（纯状态机，原 Orchestrator 改名）
    ├── ResumeAgent（任务型，由 MainAgent 通过工具调用）
    ├── InterviewAgent（后台监听者，面试阶段激活）
    └── EvalAgent（触发型，面试结束时执行）
```

---

## 三、各模块详细设计

### 3.1 MainAgent（新增）

**文件**：`src/agents/main_agent.py`

#### 定位

面试官的唯一对话入口，全程常驻。通过系统提示感知面试官偏好、候选人信息和当前会话状态；通过工具完成对话本身无法直接执行的操作（如委托简历解析、更新记忆）。

#### 系统提示结构（分层，按顺序组装）

| 层级 | 内容 | 加载时机 |
|---|---|---|
| 1 | 角色定义：面试助手，帮助面试官管理候选人、准备问题、支持面试 | 固定 |
| 2 | USER.md 全文：面试官偏好、岗位要求 | 服务启动时加载一次 |
| 3 | 当前候选人信息：姓名、职位、工作年限、技能、简历摘要、题目清单 | 选中候选人时注入，切换时替换 |

#### 对话历史管理

- 在内存中以 `list[Message]` 维护，上限 24 条，超出时截断保留最新 24 条
- 切换候选人时**只替换系统提示第 3 层**，对话历史不清空（保留上下文连续性）
- 服务重启时历史清空（不持久化对话历史，只持久化 USER.md 和面试数据）

#### 工具列表

| 工具 | 签名 | 说明 |
|---|---|---|
| `delegate_to_resume_agent` | `(pdf_path: str, instructions: str) -> dict` | 同步调用 ResumeAgent，返回解析结果和题目清单 |
| `update_user_memory` | `(content: str) -> str` | 将面试官提供的岗位要求/偏好写入 USER.md |
| `get_session_info` | `() -> dict` | 查询 InterviewController 当前 stage 和会话基本信息 |
| `get_candidate_info` | `() -> dict` | 读取当前候选人完整 profile 和题目清单 |

#### 核心方法

| 方法 | 说明 |
|---|---|
| `handle_chat(message: str) -> AsyncIterator[str]` | 处理用户消息，流式返回 |
| `set_candidate_context(profile: CandidateProfile, questions: list)` | 切换候选人时由 API 层调用，替换系统提示第 3 层 |
| `reload_user_memory()` | USER.md 更新后重新加载第 2 层（update_user_memory 工具内部调用） |

---

### 3.2 InterviewController（原 Orchestrator 改名）

**文件**：`src/agents/interview_controller.py`（原 `orchestrator.py`）

#### 变化

- **改名**：`Orchestrator` → `InterviewController`，语义更准确（它是状态机控制器，不是 AI Agent）
- **删除**：`handle_request()`、`handle_stream()` 路由方法（对话消息改由 MainAgent 直接处理）
- **删除**：`switch_agent()` 中针对 ResumeAgent 的切换逻辑（ResumeAgent 现在由 MainAgent 工具调用）
- **保留**：会话生命周期、音频管道管理、WebSocket 广播、阶段状态追踪

#### 状态机（精简后）

```
idle
  └─ start_interview() → interviewing（启动音频管道，激活 InterviewAgent）
        └─ stop_interview() → evaluating（停止音频，触发 EvalAgent）
                └─ close_session() → completed（持久化会话）
```

> 删除 `resume_analysis` 阶段：简历解析现在由 MainAgent 通过工具按需触发，不再需要独立的阶段状态。

#### 保留方法

| 方法 | 说明 |
|---|---|
| `create_session(candidate_id?)` | 创建 InterviewSession |
| `start_interview()` | 启动音频管道，激活 InterviewAgent |
| `stop_interview()` | 停止音频，flush pending round，触发 EvalAgent |
| `close_session()` | 持久化到 SQLite，重置状态 |
| `get_session_info() -> dict` | 供 MainAgent 工具查询当前状态 |
| `attach_ws_sender / detach_ws_sender` | WebSocket 连接管理 |

---

### 3.3 ResumeAgent（保留，调整触发方式）

**文件**：`src/agents/resume_agent.py`（基本不变）

#### 变化

- **触发方式**：从"由 Orchestrator 切换激活"改为"由 MainAgent 通过 `delegate_to_resume_agent` 工具同步调用"
- **接口**：新增 `execute(pdf_path: str, instructions: str) -> ResumeResult` 作为直接调用入口
- **内部逻辑**：`_parse_resume()` 和 `_generate_questions()` 保持不变

#### 调用流程

```
用户上传 PDF → 前端 POST /api/resume/upload → 文件保存，返回 file_path
    ↓
前端在聊天框显示："已上传简历，路径: {file_path}，请解析"
    ↓
MainAgent 收到消息 → LLM 决定调用 delegate_to_resume_agent(pdf_path, instructions)
    ↓
ResumeAgent.execute() 同步执行（parse → generate_questions）
    ↓
返回 {profile: CandidateProfile, questions: [...]} 给 MainAgent
    ↓
MainAgent 更新 session.candidate，调用 set_candidate_context() 刷新自身上下文
    ↓
MainAgent 流式回复用户：解析结果摘要 + 生成的题目清单
```

---

### 3.4 InterviewAgent（保留，不变）

职责和实现与当前一致：后台监听者，`SuggestionTrigger` 驱动，WebSocket 推送追问建议。由 `InterviewController.start_interview()` 激活，`stop_interview()` 停止。

---

### 3.5 EvalAgent（保留，不变）

由 `InterviewController.stop_interview()` 触发，生成评价报告并持久化。

---

### 3.6 USER.md 记忆模块

**文件**：`USER.md`（项目根目录或 `data/USER.md`）

- 纯文本 Markdown，面试官的全局记忆：招聘岗位要求、面试风格偏好等
- 服务启动时由 `MainAgent.__init__()` 加载一次，写入系统提示第 2 层
- 面试官在对话中提供新信息时（如"我们要招一个 3 年以上 Java 后端"），MainAgent 调用 `update_user_memory` 工具追加写入 USER.md，并调用 `reload_user_memory()` 刷新提示词

---

## 四、API 层变化

### 新增接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/chat` | 接收用户消息，流式转发到 MainAgent |
| `POST` | `/api/candidate/select` | 选中候选人，调用 MainAgent.set_candidate_context() |
| `POST` | `/api/resume/upload` | 上传并保存 PDF，返回 file_path（不再直接触发解析） |

### 变化接口

| 方法 | 路径 | 变化 |
|---|---|---|
| `POST` | `/api/interview/start` | 不变，调用 InterviewController.start_interview() |
| `POST` | `/api/interview/stop` | 不变，调用 InterviewController.stop_interview() |

### 删除

- 原通过 Orchestrator 路由的对话接口（UI Agent 使用的所有 HTTP 工具接口）

---

## 五、前端变化

**删除**：`src/web/ui.py` 中的 `_agent_loop` 函数及所有 UI Agent 相关逻辑（`state["agent_history"]`、工具定义、`_agent_loop` 协程）

**改为**：聊天框直接调用 `POST /api/chat`，流式接收 MainAgent 的回复并展示（打字机效果）

**候选人切换**：点击候选人列表时，调用 `POST /api/candidate/select`，不再在前端注入候选人上下文

---

## 六、实现任务清单

### Phase 1：骨架搭建
- [ ] 新建 `src/agents/main_agent.py`，实现 `handle_chat()` 和系统提示分层构建
- [ ] 创建 `USER.md` 文件（初始模板）
- [ ] 重命名 `orchestrator.py` → `interview_controller.py`，删除路由方法，精简状态机

### Phase 2：工具实现
- [ ] 实现 `delegate_to_resume_agent` 工具，封装对 ResumeAgent 的同步调用
- [ ] 实现 `update_user_memory` 工具，读写 USER.md
- [ ] 实现 `get_session_info` 工具，对接 InterviewController
- [ ] 实现 `get_candidate_info` 工具，读取 session.candidate

### Phase 3：API 层
- [ ] 新增 `POST /api/chat` 流式接口
- [ ] 新增 `POST /api/candidate/select` 接口
- [ ] 修改 `POST /api/resume/upload`，只存文件，不触发解析

### Phase 4：前端改造
- [ ] 删除 `ui.py` 中的 `_agent_loop` 和 UI Agent 代码
- [ ] 聊天框改为调用 `/api/chat`，支持流式显示
- [ ] 候选人切换改为调用 `/api/candidate/select`

### Phase 5：ResumeAgent 适配
- [ ] 新增 `ResumeAgent.execute(pdf_path, instructions)` 直接调用入口

### Phase 6：收尾
- [ ] 更新 `src/main.py` lifespan，初始化 MainAgent 单例，加载 USER.md
- [ ] 更新 `docs/arc/agents.md` 架构文档
- [ ] 更新 `docs/arc/api.md` 接口文档
