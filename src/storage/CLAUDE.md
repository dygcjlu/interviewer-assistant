# storage 模块规则

## 本模块职责
SQLite 数据持久化，使用 aiosqlite 实现异步读写。管理所有长期数据（候选人档案、面试记录、对话轮次、评价报告）。

详细设计见 `docs/arc/memory-and-storage.md`。

## 不负责
- 短期记忆（运行时 `InterviewSession` 对象，存活在 agents/ 层）
- 任何 LLM 调用
- 音频文件的读写（只存文件路径引用，不存二进制）

## SQLite 表结构

| 表 | 说明 |
|----|------|
| `Candidate` | 候选人基本信息 + `profile_json`（跨面试积累的档案标签） |
| `Interview` | 面试会话记录（含 `context_summary`） |
| `ConversationRound` | 逐轮对话文本 + 音频路径引用 |
| `EvalReport` | 评价报告 JSON |
| `TokenUsage` | 每次 LLM 调用的 token 消耗统计 |

## 接口约定

- 所有函数均为 `async`，返回类型使用 `src/models/` 中定义的 dataclass，绝不返回裸 dict 或 sqlite3.Row。
- 写操作在事务中执行；绝不手动管理 `commit()`（使用 `async with conn` 上下文管理器）。

## 禁止事项

- 绝不在此模块引入 ORM 框架（如 SQLAlchemy）。
- 绝不存储二进制音频数据。
- 绝不被上层以外的模块（web/、agents/）直接调用；通过 `MemoryModule` 接口间接访问。

## 进度记录义务

每完成一个子任务，向 `progress/infra-storage.md` 追加记录。
