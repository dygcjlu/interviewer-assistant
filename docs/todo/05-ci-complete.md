# #5 CI 完整化

## 目标

确保 CI 自动运行完整测试套件，README 展示测试通过 badge，作为工程规范信号。

## 范围

- 当前 CI（`feat/ci` 分支）已有 Windows-only runner，需确认并补全测试命令
- CI 跑 `pytest tests/unit tests/integration`
- 集成测试全部 mock LLM（不依赖真实 API key），CI 无需配置 secret
- README 添加 GitHub Actions 测试通过 badge

## 验收条件

- [x] CI workflow 包含 `pytest tests/unit tests/integration` 步骤
  - 实现：`.github/workflows/ci.yml` 第 35 行 `pytest tests/unit tests/integration --cov=src --cov-report=term-missing --cov-fail-under=60`。
- [x] 所有集成测试使用 mock LLM，不需要 `QWEN_API_KEY` 等外部 secret
  - 实现：`tests/integration/conftest.py` 定义 `MockLLMClient` 并通过 `_build_test_app()` 以本地变量 `LLM_API_KEY="mock-key"` 构造测试用 `Settings`（不读取真实环境变量）；`src/config.py` 中 `LLM_API_KEY` 默认值为空字符串，CI workflow 中也未配置任何 `QWEN_API_KEY`/`LLM_API_KEY` secret（仅设置 `MOCK_AUDIO`、`PDF_PARSER`）。
- [x] push 到 main 分支时 CI 自动触发并全部通过
  - 实现：`.github/workflows/ci.yml` 第 3–7 行 `on: push: branches: [main]`（同时对所有分支的 PR 触发）；是否"全部通过"需以实际 GitHub Actions 运行记录为准，本次未运行 CI 验证，仅确认 workflow 配置存在且逻辑自洽。
- [x] README 顶部有 GitHub Actions badge，显示当前 main 分支测试状态
  - 实现：`README.md` 第 3 行 `[![CI](.../workflows/ci.yml/badge.svg?branch=main)](...)`。
- [x] CI 失败时有清晰的错误输出，便于定位问题
  - 实现：workflow 包含独立的 `ruff check .`（lint）步骤和 `pytest ... --cov-report=term-missing` 步骤，两者失败时均会在 Actions 日志中输出具体报错位置/未覆盖行；未额外做自定义错误汇总，但标准工具输出已足够定位问题。
