# Contributing

感谢你愿意参与 Interviewer Assistant。

## 开发环境

1. 使用 Python 3.12+。
2. 创建项目虚拟环境并安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

3. 复制 `.env.example` 为 `.env`，按需填写 LLM 和 STT 凭据。
4. 使用 `MOCK_AUDIO=true` 可以在没有真实音频设备或 STT 凭据时调试主要流程。

## 提交变更

- 保持改动聚焦，一次 PR 尽量解决一个问题。
- 修改功能后，同步更新相关文档，尤其是 `docs/arc/` 下的架构文档。
- 不要提交真实候选人简历、录音、数据库、日志、API key、招聘计划或个人偏好记忆。
- 如果新增配置项，请同步更新 `.env.example` 和 README。
- 如果修改用户可见行为，请补充或更新测试。

## 测试

提交前请尽量运行：

```bash
python -m pytest
```

如果某些测试依赖本地音频设备、外部 API 或平台能力，请在 PR 中说明未覆盖的部分和原因。

## Commit 风格

建议使用简洁的 Conventional Commits 风格，例如：

- `feat: add mock interview playback`
- `fix: handle empty resume upload`
- `docs: update open source setup guide`
- `test: cover transcription manager`

## Pull Request

PR 描述建议包含：

- 变更目的
- 主要实现点
- 测试方式
- 任何隐私、安全或平台兼容性影响
