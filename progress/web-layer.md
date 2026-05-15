# Progress: web-layer

- [2026-05-15] 完成 src/config.py（pydantic-settings 双层配置）
- [2026-05-15] 完成 src/web/schemas.py（Pydantic 请求模型）
- [2026-05-15] 完成 src/web/routes.py（13 个 REST 端点）
- [2026-05-15] 完成 src/web/websocket.py（WebSocket 处理器，支持 request_suggestion / manual_input / set_trigger_mode / switch_agent / heartbeat）
- [2026-05-15] 完成 src/web/app.py（FastAPI 工厂，托管静态文件）
- [2026-05-15] 完成 src/main.py（全量依赖注入启动入口）
- [2026-05-15] 完成 tests/test_web/test_routes.py（11 项路由测试，全部通过）
