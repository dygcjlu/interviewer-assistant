# frontend 模块规则

## 本模块职责
Vue 3 SPA，使用 Vite 构建。面向本地单用户（面试官），通过浏览器访问 localhost。

详细设计见 `docs/arc/web-layer.md`（前端部分）。

## 页面结构

| 路由 | 页面 | 功能 |
|------|------|------|
| `/` | Home | 候选人列表 + 新建面试入口 |
| `/prepare` | Prepare | 简历上传 + 题目清单审核 |
| `/console` | Console | 实时面试控制台（转写 + 建议） |
| `/report` | Report | 评价报告查看 |

## 技术约定

- **状态管理**：Pinia
- **HTTP 客户端**：axios（统一封装，base URL = `http://localhost:8000`）
- **WebSocket**：原生 WebSocket，在 Pinia store 中管理连接生命周期
- **组件库**：可引入（Element Plus 或 Naive UI），但绝不引入 React 相关库

## 禁止事项

- 绝不在前端持久化敏感信息（API Key 等）。
- 绝不绕过后端直接调用 LLM API。
- 绝不在组件中直接 fetch，统一通过 API service 层调用。
- 生产构建输出到 `frontend/dist/`，由 FastAPI 静态托管，绝不需要独立 Nginx。

## 进度记录义务

每完成一个子任务，向 `progress/frontend.md` 追加记录。
