# framework 模块规则

## 本模块职责
Agent 运行框架的通用能力：Skill 加载、Tool 注册调度、上下文管理、记忆模块、Prompt 构建。

详细设计见 `docs/arc/context-and-prompt.md`、`docs/arc/skill-and-tool.md`、`docs/arc/memory-and-storage.md`。

## 不负责
- Agent 业务逻辑（见 agents/）
- 直接调用 LLM（通过注入的 `LLMClient` 实例调用）
- 直接操作数据库（通过注入的 Storage 实例调用）

## 关键组件

| 文件 | 组件 |
|------|------|
| `skill.py` | `SkillLoader`（从 `skills/{name}/SKILL.md` 动态加载） |
| `tool.py` | `ToolRegistry`（注册、调度 Tool，串行执行） |
| `context.py` | `ContextManager`（三区结构 + 三阶段压缩，后台异步执行） |
| `memory.py` | `MemoryModule`（短期 in-session + 长期 SQLite 接口） |
| `prompt_builder.py` | `PromptBuilder`（7 层 prompt 组装，唯一输出 `list[Message]` 的模块） |

## 接口约定

- `PromptBuilder.build()` 是唯一输出 `list[Message]` 的入口，其他模块绝不自行拼接 messages。
- `ContextManager` 压缩在后台异步运行，绝不阻塞 `PromptBuilder.build()` 调用。
- `SkillLoader` 从文件系统加载 Skill，与业务代码完全解耦，绝不硬编码 Skill 内容。
- `ToolRegistry` 串行执行 Tool（单进程 asyncio，无并发 Tool 执行）。

## 禁止事项

- 绝不在 framework/ 引入具体的 Agent 类（避免循环依赖）。
- 绝不在压缩逻辑中直接修改 `InterviewSession`（通过回调通知 Agent）。

## 进度记录义务

每完成一个子任务，向 `progress/context-prompt.md` 追加记录。
