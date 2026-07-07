# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- 工程规范：接入 ruff、black、isort、pytest-cov 开发依赖（`requirements-dev.txt`）
- 在 `pyproject.toml` 中配置 ruff、black、isort、pytest 与覆盖率工具
- CI 新增 ruff lint 步骤
- CI 覆盖率门禁：`--cov-fail-under=60`

### Changed
- 全库一次性应用 ruff/black/isort 格式化（无行为变更）
- 同步 `docs/` 与待办状态，使其与当前实际实现进度一致

### Fixed
- `test_volc_stt` 对本地 `.env` 环境变量的隐式依赖（测试隔离）
- 移除被 `pytest.ini` 遮蔽的无效 `[tool.pytest.ini_options]` 配置
