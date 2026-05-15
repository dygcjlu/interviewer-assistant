# models 模块规则

## 本模块职责
定义所有跨模块共享的核心数据结构（dataclass）。是各模块间的**数据契约**。

## 不负责
- 任何业务逻辑
- 任何数据库操作
- 任何 IO 操作

## 文件归属

| 文件 | 类型 |
|------|------|
| `session.py` | InterviewStage, InterviewSession, ConversationRound, SessionMetadata, InterviewQuestion, TokenUsageInfo |
| `candidate.py` | CandidateProfile, Education, WorkExperience, ProjectExperience |
| `evaluation.py` | EvalReport, DimensionScore |
| `message.py` | Message, ToolCallInfo, FunctionCallInfo |

## 禁止事项

- 绝不在此目录引入任何外部依赖（只允许 `from __future__ import annotations` 和标准库）。
- 所有字段类型必须明确，绝不使用 `Any`。
- 绝不添加业务方法（`__post_init__` 验证除外）。
