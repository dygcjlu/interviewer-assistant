# #5 CI 完整化

## 目标

确保 CI 自动运行完整测试套件，README 展示测试通过 badge，作为工程规范信号。

## 范围

- 当前 CI（`feat/ci` 分支）已有 Windows-only runner，需确认并补全测试命令
- CI 跑 `pytest tests/unit tests/integration`
- 集成测试全部 mock LLM（不依赖真实 API key），CI 无需配置 secret
- README 添加 GitHub Actions 测试通过 badge

## 验收条件

- [ ] CI workflow 包含 `pytest tests/unit tests/integration` 步骤
- [ ] 所有集成测试使用 mock LLM，不需要 `QWEN_API_KEY` 等外部 secret
- [ ] push 到 main 分支时 CI 自动触发并全部通过
- [ ] README 顶部有 GitHub Actions badge，显示当前 main 分支测试状态
- [ ] CI 失败时有清晰的错误输出，便于定位问题
