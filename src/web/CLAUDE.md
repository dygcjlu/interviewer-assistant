# web 模块规则

## 本模块职责
FastAPI REST API + WebSocket 服务端。唯一对外暴露 HTTP 接口的模块。

详细设计见 `docs/arc/web-layer.md`。

## 不负责
- 任何业务逻辑（委托给 Orchestrator）
- 数据库操作（委托给 MemoryModule / Storage）
- Prompt 构建

## 端点列表

| 端点 | 方法 |
|------|------|
| `/api/resume/upload` | POST |
| `/api/resume/profile` | GET |
| `/api/interview/questions` | GET / PUT |
| `/api/interview/start` | POST |
| `/api/interview/stop` | POST |
| `/api/session/switch` | POST |
| `/api/interview/suggest` | POST |
| `/api/interview/eval` | GET |
| `/api/candidates` | GET |
| `/api/candidates/{id}/history` | GET |
| `/api/recordings/{session_id}/rounds/{round}` | GET |
| `/api/session/current` | GET |
| `/ws/interview` | WebSocket |

## WebSocket 消息类型

上行（前端 → 服务端）：`switch_agent`、`manual_suggest`、`heartbeat`
下行（服务端 → 前端）：`transcript`、`suggestion`、`status`、`token_usage`、`error`

## 禁止事项

- 绝不在 Route Handler 中包含业务逻辑，所有调用转发给 `Orchestrator`。
- 绝不直接访问 SQLite（通过 MemoryModule 访问）。
- 生产环境由 FastAPI 托管 Vue 3 静态文件，绝不独立启动前端 dev server。
- 错误响应统一使用 `{"error": {"code": ..., "message": ..., "detail": ...}}` 格式。

## 进度记录义务

每完成一个子任务，向 `progress/web-layer.md` 追加记录。
