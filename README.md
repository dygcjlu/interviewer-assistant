# Interviewer Assistant

本地运行的 AI 面试辅助工具，面向单个面试官管理多名候选人。它可以解析简历、生成面试问题、实时展示双声道转写、给出追问建议，并在面试结束后生成评价报告。

> 当前项目主要面向中文技术面试场景。真实候选人简历、录音、数据库、日志和面试官偏好记忆都默认保存在本地，不应提交到 Git。

## 核心功能

- 简历管理：上传候选人 PDF 简历，解析并保存为 Markdown。
- 面试准备：基于候选人简历和岗位要求生成面试问题及预期答案要点。
- 实时转写：在 Windows 上通过 WASAPI 采集候选人和面试官双声道音频，并接入百度或讯飞实时语音识别。
- 追问建议：候选人回答后，结合当前问题和回答内容生成追问建议。
- 面试评价：根据完整面试记录生成候选人评价报告。
- Mock 调试：非 Windows 或无音频设备时，可用脚本模拟转写流程。

## 技术栈

- Python 3.12+
- FastAPI + uvicorn
- NiceGUI
- 本地文件系统存储（候选人档案、面试记录、评价报告）
- OpenAI 兼容 LLM API（默认通义千问）
- Windows WASAPI + 百度/讯飞实时 ASR

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/dygcjlu/interviewer-assistant.git
cd interviewer-assistant
```

### 2. 创建虚拟环境

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

Linux / macOS:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

### 3. 配置环境变量

复制示例配置：

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

至少需要配置：

- `QWEN_API_KEY`：OpenAI 兼容 LLM API key。
- `QWEN_API_BASE_URL`：OpenAI 兼容接口地址，默认使用通义千问兼容模式。
- `QWEN_MODEL`：LLM 模型名。

如果需要真实语音识别，请按 `STT_ENGINE` 配置百度或讯飞凭据。只体验流程可以设置：

```env
MOCK_AUDIO=true
```

### 4. 启动服务

```bash
python -m src.main
```

访问 `http://127.0.0.1:8000`。

也可以使用脚本启动：

```powershell
.\scripts\start-dev.ps1
```

```bash
./scripts/start-dev.sh
```

## 配置说明

主要配置项见 [.env.example](.env.example)。

- LLM：`QWEN_API_KEY`、`QWEN_API_BASE_URL`、`QWEN_MODEL`、`LLM_TIMEOUT_SEC`、`LLM_MAX_RETRIES`
- 服务：`HOST`、`PORT`、`DEBUG`
- 存储：`CANDIDATES_DIR`、`RECORDINGS_DIR`
- 上下文：`CONTEXT_WINDOW_SIZE`、`CONTEXT_TOKEN_BUDGET`、`CONTEXT_COMPRESSION_THRESHOLD`
- STT：`STT_ENGINE`、`BAIDU_*`、`XUNFEI_*`
- PDF：`PDF_PARSER`、`QWEN_VL_MODEL`、`MINERU_*`
- 调试：`MOCK_AUDIO`、`MOCK_AUDIO_SCRIPT`

## 平台说明

- Windows：支持 WASAPI 双声道音频采集，适合真实面试场景。
- Linux / macOS：默认使用 Mock 音频或手动输入流程；真实双声道采集能力未作为主要目标。
- 所有平台：可通过 `MOCK_AUDIO=true` 使用 `data/mock_script.json` 模拟面试对话。

## 测试

```bash
python -m pytest
```

如果本地没有安装测试依赖，请先安装项目依赖并确认当前 Python 环境为项目虚拟环境。

## 数据与隐私

> **重要提示（S-17）**：本工具仅供本机本地使用。所有候选人简历、面试录音、转写记录和评价报告均以**明文**存储在本地磁盘，无任何加密。请使用操作系统提供的磁盘加密（Windows BitLocker / macOS FileVault）保护存储介质。

本项目默认在本地文件系统保存运行数据：

- `USER.md`：面试官岗位要求与偏好记忆，运行时自动生成。
- `candidates/`：候选人长期数据（简历、面试记录、评价报告）。
- `recordings/`：面试录音文件。
- `resumes/`：上传的 PDF 简历及解析后的 Markdown。
- `logs/`：运行日志（可能包含简历片段和面试对话，受 `LOG_SENSITIVE` 配置控制）。
- `conversations/`：Agent 对话历史。

这些目录已在 [.gitignore](.gitignore) 中忽略。**公开提交前请确认没有真实候选人资料、录音、API key、招聘计划或内部业务信息。**

**使用建议**：
- 勿将 `HOST` 改为 `0.0.0.0`（默认 `127.0.0.1` 仅本机访问）；若需局域网访问，须同时配置鉴权。
- 定期清理不再需要的候选人数据：删除对应的 `candidates/{id}/`、`recordings/`、`resumes/` 下的文件。
- 可使用 `scripts/cleanup-old.ps1`（Windows）或 `scripts/cleanup-old.sh`（Linux/macOS）按最后访问时间批量清理历史数据。

## 文档

架构文档位于 [docs/arc](docs/arc)：

- [总体架构](docs/arc/overview.md)
- [Agent 设计](docs/arc/agents.md)
- [API 与 WebSocket](docs/arc/api.md)
- [核心流程](docs/arc/flows.md)
- [存储设计](docs/arc/storage.md)
- [提示词组装](docs/arc/prompt-assembly.md)
- [上下文与记忆](docs/arc/context-memory.md)

## 贡献

欢迎提交 issue 和 pull request。贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题请参考 [SECURITY.md](SECURITY.md)。

## 许可证

当前按 MIT License 草案准备，见 [LICENSE](LICENSE)。正式发布前请确认该许可证符合你的开源目标。
