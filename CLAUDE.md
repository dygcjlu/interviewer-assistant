# CLAUDE.md

## 项目定位

面向**单个面试官**的本地面试辅助工具，支持管理**多名候选人**。核心工作流如下：

1. **简历管理**：上传候选人 PDF 简历，自动解析为 Markdown 文档存档。
2. **面试准备**：基于候选人简历与岗位要求（可选），生成面试问题列表及每题的预期答案要点。岗位要求通过与面试官的对话获取，持久化保存到 `USER.md` 记忆文档中。
3. **实时转写**：面试过程中独立采集候选人和面试官的双声道音频，实时转写为文本并在 Web 界面同步展示。
4. **追问建议**：候选人回答完毕后，将面试官问题与候选人回答一并发送给 LLM，自动生成追问建议供面试官参考。
5. **面试评价**：面试结束后，根据完整对话记录由 LLM 生成候选人面试评价报告。

## 技术栈

Python 3.12+，FastAPI + uvicorn，NiceGUI（同进程前端），SQLite + aiosqlite，OpenAI 兼容 LLM（默认通义千问），Windows WASAPI + 百度实时 ASR（非 Windows 使用 Mock）。

## 配置

从 `.env` 加载（`src/config.py`）。主要项：`QWEN_API_KEY`、`QWEN_MODEL`、`HOST`、`PORT`、`DB_PATH`、`RECORDINGS_DIR`、`CONTEXT_TOKEN_BUDGET`（默认 80000）。

## 运行环境

本项目使用项目目录下的 `.venv` 虚拟环境（Python 3.12）。

```bash
# 激活环境
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux/macOS

# 安装依赖（首次或 requirements.txt 变更后）
pip install -r requirements.txt
```

> 注意：机器上同时存在名为 `interview-assistant` 的 conda 环境，但该环境供其他项目使用，**本项目不使用它**。

## 启动

```bash
python -m src.main
# http://127.0.0.1:8000
```

也可以通过 `scripts/` 目录下的脚本启动和停止服务：

```powershell
# Windows PowerShell
.\scripts\start-dev.ps1   # 启动
.\scripts\stop-dev.ps1    # 停止
```

```bash
# Linux / macOS
./scripts/start-dev.sh    # 启动
./scripts/stop-dev.sh     # 停止
```

## 目录结构

```
src/
├── main.py          # 启动入口，lifespan() 手动组装所有依赖
├── config.py        # Settings 单例
├── web/             # FastAPI routes、WebSocket、NiceGUI UI
├── agents/          # Orchestrator + 三个 Agent + prompts
├── framework/       # ContextManager、PromptBuilder、SkillLoader、ToolRegistry
├── llm/             # OpenAI 兼容客户端
├── audio/           # 采集、STT、录音、SuggestionTrigger
├── tools/           # parse_resume_pdf、skill_tools、interview_control_tools
├── storage/         # Database、Repository、MemoryModule
└── models/          # InterviewSession、CandidateProfile、EvalReport 等数据模型

skills/              # SKILL.md 面试技巧文件
recordings/          # 录音文件，按 {session_id}/ 分目录
resumes/             # 上传 PDF 和生成的 Markdown
logs/                # 运行日志 (logs\app.log)
```


## 架构文档

**详细设计请查阅 `docs/arc/` 目录：**

| 文档 | 内容 |
|---|---|
| `overview.md` | 系统分层图、完整目录说明、技术栈、启动流程 |
| `agents.md` | Orchestrator 状态机、三个 Agent 职责与核心方法、InterviewSession 结构 |
| `api.md` | 所有 REST 接口、WebSocket 消息类型、接口与 Agent 操作对应关系 |
| `flows.md` | 简历上传、面试开始/结束、实时转写与追问、评价生成等主要流程时序图 |
| `storage.md` | SQLite 表结构、MemoryModule 职责、录音目录规则、DB fallback 逻辑 |
| `prompt-assembly.md` | 各 Agent 动态组装提示词的机制：MainAgent 三层系统提示、PromptBuilder 七层结构、各层内容与更新时机 |
| `context-memory.md` | 上下文与记忆管理：ContextManager 滑动窗口与异步压缩、USER.md 面试官记忆、候选人长期记忆与历史整合 |

**功能变更后需同步更新对应的 `docs/arc/` 文档，此目录下的文档应该保持简洁，只描述核心逻辑即可。**
