# audio 模块规则

## 本模块职责
音频采集、语音识别（STT）、录音管理、转写管理。

详细设计见 `docs/arc/audio-and-stt.md`。

## 不负责
- LLM 调用
- 数据库读写（录音文件路径由 storage/ 负责持久化）
- Agent 业务逻辑

## 关键组件

| 文件 | 组件 |
|------|------|
| `protocol.py` | `AudioCapturer`、`STTEngine` Protocol 定义（不可修改） |
| `mock.py` | `MockAudioCapturer`、`MockSTTEngine`（仅开发/测试） |
| `wasapi.py` | `WasapiCapturer`（Windows 生产实现，不在 Linux 上 import） |
| `baidu_stt.py` | `BaiduRealtimeSTT` WebSocket 实现 |
| `stream.py` | `AudioStreamBridge`（回调 → 双 STT + 录音分流） |
| `recorder.py` | `AudioRecorder`（完整录音 + 轮次切片） |
| `transcription.py` | `TranscriptionManager`（STT 结果 → WebSocket 推送 + 触发建议） |

## 硬约束

- **绝不直接 import pyaudio / soundcard / wasapi**；开发阶段必须使用 `MockAudioCapturer`。
- `AudioCapturer` 和 `STTEngine` 是 Protocol，实现必须满足接口而非继承。
- `AudioStreamBridge` 不负责关闭 STT/Recorder，由上层 Orchestrator 管理生命周期。
- 音频回调是唯一允许在非 async 上下文执行的代码（soundcard 回调线程）；使用 `asyncio.run_coroutine_threadsafe` 桥接到事件循环。

## 进度记录义务

每完成一个子任务，向 `progress/audio-stt.md` 追加记录。
