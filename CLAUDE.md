# 面试助手 — 项目概要（for 开发 Agent）

## 项目定位

面向技术面试场景的**本地单用户面试辅助工具**。面试官在本机运行，通过浏览器访问 localhost 使用。

核心价值：实时采集面试双方音频 → 自动转写 → 由 AI 生成追问建议，辅助面试官提问。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python asyncio 单进程，FastAPI + uvicorn |
| 前端 | Vue 3 SPA（Vite，生产环境由 FastAPI 托管静态文件） |
| LLM | OpenAI 兼容 SDK，通过 `base_url` 切换国产模型（通义/DeepSeek 等） |
| 语音 | WASAPI 双声道采集 + 百度实时 ASR（均为可替换 Protocol 抽象） |
| 存储 | SQLite（aiosqlite），本地文件存录音 / 日志 / 轨迹 |

---

## 系统分层（依赖方向：下 → 上创建，上 → 下调用）

```
Web 层       FastAPI REST + WebSocket
Agent 层     Orchestrator + ResumeAgent / InterviewAgent / EvalAgent
框架层       SkillLoader / ToolRegistry / ContextManager / MemoryModule / PromptBuilder
基础设施层   LLMClient / STTEngine / AudioCapturer / AudioRecorder / SQLite
```

---

## 核心模块速览

- **Orchestrator**：管理面试会话生命周期，驱动三 Agent 自由切换；Agent 间通过共享 `InterviewSession` 交换数据，不直接通信。
- **InterviewAgent**：实时面试核心，负责流式生成追问建议，持有 `SuggestionTrigger`（候选人沉默 2 秒自动触发 / 手动触发）。
- **ContextManager**：三区结构（固定区 + 摘要区 + 滑动窗口），三阶段压缩（剪枝 → 截断 → LLM 摘要）控制 token 预算，后台异步执行。
- **PromptBuilder**：唯一输出 `messages` 列表的模块，按 7 层顺序组装 prompt（system → skill → memory → 历史摘要 → 滑动窗口）。
- **SkillLoader**：从 `skills/{name}/SKILL.md` 文件系统动态加载，与业务代码解耦。
- **TranscriptionManager**：STT 与上层的中间层，按说话人分流、推送 WebSocket、触发建议、标记轮次边界。
- **MemoryModule**：短期记忆（运行时 `InterviewSession`）+ 长期记忆（SQLite 跨会话历史）。

---

## 目录结构（关键路径）

```
src/
├── main.py               # 启动入口，手动组装所有依赖
├── config.py             # 双层配置（config.yaml + .env → pydantic Settings）
├── web/                  # FastAPI routes + WebSocket
├── agents/               # BaseAgent ABC + 三个 Agent + Orchestrator
├── framework/            # skill / tool / context / memory / prompt_builder
├── llm/                  # OpenAI 兼容客户端
├── audio/                # 采集 / STT / 录音 / 转写管理
├── tools/                # 具体工具（简历解析等）
├── storage/              # SQLite CRUD
└── models/               # 共享数据结构（InterviewSession / CandidateProfile / EvalReport）
frontend/                 # Vue 3（Home / Prepare / Console / Report）
skills/                   # 内置 Skill 目录
docs/arc/                 # 详细架构文档
```

---

## 关键约定

1. **不引入 LangGraph / AutoGen**：Agent 框架完全自建。
2. **Protocol 抽象**：`AudioCapturer` 和 `STTEngine` 均为 Protocol，实现可替换。
3. **单进程 asyncio**：所有 IO 操作均为 async，不使用多进程/多线程（音频回调除外）。
4. **层间隔离**：下层不感知上层；同层通过 `InterviewSession` 或事件通信。
5. **配置分离**：业务参数放 `config.yaml`，API Key 等敏感信息放 `.env`。

注意：当你在进行方案调研时，若访问中国之外的网站或者下载数据速度较慢时，可以通过"clashctl on"开启vpn. "clashctl off"是关闭vpn的命令