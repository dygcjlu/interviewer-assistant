# agents 模块规则

## 本模块职责
Orchestrator + 三个 Agent（ResumeAgent / InterviewAgent / EvalAgent）的业务逻辑。

详细设计见 `docs/arc/agent-orchestrator.md`。

## 不负责
- Prompt 构建（委托给 `framework/prompt_builder.py`）
- 上下文压缩（委托给 `framework/context.py`）
- 数据库操作（通过 `MemoryModule` 间接访问）
- 音频管理（通过 `AudioManager` / `TranscriptionManager` 间接访问）

## 关键组件

| 文件 | 组件 |
|------|------|
| `base.py` | `BaseAgent` ABC（含 `run()` / `stop()` 抽象方法） |
| `orchestrator.py` | `Orchestrator`（会话生命周期 + Agent 切换状态机） |
| `resume_agent.py` | `ResumeAgent`（简历解析 + 题目生成） |
| `interview_agent.py` | `InterviewAgent`（实时追问建议 + SuggestionTrigger） |
| `eval_agent.py` | `EvalAgent`（评价报告生成） |

## 接口约定

- Agent 间通过共享的 `InterviewSession` 交换数据，**绝不直接相互调用**。
- Agent 切换只能通过 `Orchestrator.switch_agent()` 触发，绝不在 Agent 内部自行切换。
- `InterviewAgent` 持有 `SuggestionTrigger`（候选人沉默 2 秒自动触发 / 手动触发），不直接调用 LLM。

## 禁止事项

- 绝不在 agents/ 直接 import storage/（通过 MemoryModule 访问）。
- 绝不在 agents/ 直接调用 `asyncio.create_task` 启动无主 Task（由 Orchestrator 统一管理）。
- 绝不引入 LangGraph / AutoGen 等外部 Agent 框架。

## 进度记录义务

每完成一个子任务，向 `progress/agent-framework.md` 追加记录。
