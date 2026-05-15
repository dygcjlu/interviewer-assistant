# 后端总规

## 本层职责
所有 Python 后端代码（FastAPI + Agent + 框架层 + 基础设施层）。

## 绝不违反的硬约束

- **绝不引入 LangGraph / AutoGen / LangChain**：Agent 框架完全自建。
- **绝不使用多进程或多线程**：除音频回调外，所有 IO 操作均为 `async`，不使用 `threading.Thread` / `multiprocessing`。
- **绝不跨层直接调用**：下层不感知上层（基础设施层不 import agents/，agents/ 不 import web/）。
- **绝不绕过 Protocol 抽象**：不直接 import pyaudio、soundcard 或 wasapi；开发阶段使用 `MockAudioCapturer`。
- **绝不在生产代码中 `from src.audio.mock import *`**：Mock 实现仅在测试和开发环境使用。

## 分层依赖方向

```
web/ → agents/ → framework/ → llm/ / audio/ / storage/
```
上层可以 import 下层，下层绝不 import 上层。同层通过 `InterviewSession` 或事件通信。

## 配置规则

- 业务参数放 `config.yaml`，API Key 等敏感信息放 `.env`。
- 绝不在代码中硬编码 API Key / Base URL。

## 进度记录义务

每完成一个子任务，向 `progress/<模块名>.md` 追加一条记录（格式：`- [日期] 完成了什么`）。
