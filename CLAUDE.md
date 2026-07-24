# CLAUDE.md

## 项目定位

面向**单个面试官**的本地面试辅助工具，支持管理**多名候选人**。核心工作流如下：

1. **简历管理**：上传候选人 PDF 简历，自动解析为 Markdown 文档存档。
2. **面试准备**：基于候选人简历与岗位要求（可选），生成面试问题列表及每题的预期答案要点。岗位要求通过与面试官的对话获取，持久化保存到 `USER.md` 记忆文档中。
3. **实时转写**：面试过程中独立采集候选人和面试官的双声道音频，实时转写为文本并在 Web 界面同步展示。
4. **追问建议**：候选人回答完毕后，将面试官问题与候选人回答一并发送给 LLM，自动生成追问建议供面试官参考。
5. **面试评价**：面试结束后，根据完整对话记录由 LLM 生成候选人面试评价报告。

## 技术栈

Python 3.12+，FastAPI + uvicorn，NiceGUI（同进程前端），`candidates/` 文件系统存储，OpenAI 兼容 LLM（经 `LLM_PROVIDER` / ProviderProfile），Windows WASAPI + 百度/讯飞/火山实时 ASR（非 Windows 或 `MOCK_AUDIO` 使用 Mock）。

## 配置

从 `.env` 加载（`src/config.py`，`get_settings()`）。主要项：`LLM_API_KEY`、`LLM_MODEL`、`LLM_PROVIDER`、`HOST`、`PORT`、`CANDIDATES_DIR`、`RECORDINGS_DIR`、`CONTEXT_TOKEN_BUDGET`（默认 80000）、`STT_ENGINE`、`PDF_PARSER`。

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
├── config.py        # Settings 单例（get_settings()）
├── web/             # FastAPI routes、WebSocket、NiceGUI UI
├── agents/          # MainAgent、InterviewController、Resume/Interview/Eval Agent
├── framework/       # ContextManager、PromptBuilder、SkillLoader、ToolRegistry
├── llm/             # OpenAI 兼容客户端 + ProviderProfile
├── audio/           # 采集、STT、录音、SuggestionTrigger
├── tools/           # dispatch_to_agent、parse_resume_pdf、manage_user_memory 等
├── storage/         # MemoryModule Facade、Candidate/Interview/Eval Store、USER.md
├── models/          # InterviewSession、CandidateProfile、EvalReport 等
└── utils/           # 原子写、metrics、PDF 导出

skills/              # SKILL.md 面试技巧文件
candidates/          # 候选人档案（文件系统）
recordings/          # 录音文件，按 {session_id}/ 分目录
resumes/             # 临时上传的简历 PDF
logs/                # 运行日志 (logs/app.log)
```

## 编码规范

- **KISS / YAGNI**：只写当前需要的代码；不做投机抽象与多余配置。
- **不可变优先**：返回新对象，避免原地修改；数据类优先 `@dataclass(frozen=True)`。
- **文件与函数**：按功能拆分，单文件建议 200–400 行、上限约 800 行；函数保持短小，早返回优于深层嵌套。
- **边界校验**：校验外部输入与 API 数据；显式处理错误，UI 友好、服务端记日志，禁止吞异常。
- **Python**：PEP 8 + 类型注解；格式化用 black / isort，lint 用 ruff；魔法数字提成命名常量；改动只触及需求相关代码，匹配现有风格。

## 测试要求

- 新功能与 bugfix 优先 **TDD**（先写失败测试 → 最小实现 → 重构）。
- 用 **pytest**；单元 / 集成覆盖核心逻辑，关键用户流可补 E2E。
- 测试命名描述行为（如 `test_returns_empty_when_no_match`）；结构用 Arrange-Act-Assert。
- 跑测：`pytest --cov=src --cov-report=term-missing`（目标覆盖率约 80%+）。

## 架构文档

**详细设计请查阅 `docs/arc/` 目录：**

| 文档 | 内容 |
|---|---|
| `overview.md` | 系统分层图、目录说明、技术栈、启动组装（含 STT/volc、WAL 扫描） |
| `agents.md` | MainAgent / InterviewController / ResumeAgent / InterviewAgent / EvalAgent、InterviewSession |
| `api.md` | REST 接口、WebSocket 消息类型、接口与 Agent 操作对应关系 |
| `flows.md` | 简历解析、面试开始/结束、实时转写与追问、评价、问题覆盖等时序 |
| `storage.md` | `candidates/` 文件系统、MemoryModule Facade、WAL、录音目录、问题清单 |
| `prompt-assembly.md` | MainAgent 三层系统提示、PromptBuilder 七层结构、EvalAgent 自建 messages |
| `context-memory.md` | ContextManager、USER.md、候选人长期记忆、rounds.jsonl 崩溃恢复 |
| `llm-providers.md` | ProviderProfile、LLM_* / VL_LLM_* 配置、多平台接入 |

**功能变更后需同步更新对应的 `docs/arc/` 文档，此目录下的文档应该保持简洁，只描述核心逻辑即可。**
