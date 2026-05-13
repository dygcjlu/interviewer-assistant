# 音频与语音识别

## 1. 音频模块（抽象接口设计）

将 `demo/audio` 迁移到 `src/audio/`，重构为接口 + 实现的分离结构。

### 1.1 AudioCapturer 接口

```python
from typing import Protocol, AsyncIterator, Callable

class AudioFrame:
    """音频帧数据"""
    data: bytes              # PCM 音频数据
    source: str              # "candidate" | "interviewer" | "mixed"
    timestamp: float         # 时间戳

class AudioCapturer(Protocol):
    """音频采集抽象接口"""
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def set_on_frame(self, callback: Callable[[AudioFrame], None]) -> None: ...
    @property
    def is_running(self) -> bool: ...
```

当前实现：`WasapiCapturer` — 基于 soundcard 库，WASAPI Loopback + 麦克风双声道采集（Windows）。

### 1.2 STTEngine 接口

```python
class TranscriptSegment:
    """语音识别片段"""
    text: str
    source: str              # "candidate" | "interviewer"
    is_final: bool
    start_time: float | None
    end_time: float | None
    timestamp: datetime

class STTEngine(Protocol):
    """语音转文字抽象接口"""
    async def connect(self) -> None: ...
    async def send_audio(self, audio_data: bytes) -> None: ...
    async def receive(self) -> AsyncIterator[TranscriptSegment]: ...
    async def close(self) -> None: ...
```

当前实现：`BaiduRealtimeSTT` — 百度实时语音识别 WebSocket API。

未来可替换为 FunASR、Whisper 等实现。

**双声道双实例架构**：面试官和候选人各自独立创建一个 STTEngine 实例（两个 WebSocket 连接），互不干扰。STTEngine 接口本身不感知声道概念，由上层 AudioStreamBridge 按 source 分流。

---

## 2. AudioStreamBridge（音频流桥接）

桥接 `AudioCapturer` 回调到双 STT 实例 + 录音器，位于 `src/audio/stream.py`。

### 2.1 接口定义

```python
class AudioStreamBridge:
    """桥接 AudioCapturer 回调到双 STT 实例 + 录音器"""

    def __init__(
        self,
        candidate_stt: STTEngine,
        interviewer_stt: STTEngine,
        recorder: AudioRecorder,
    ): ...

    async def on_frame(self, frame: AudioFrame) -> None:
        """AudioCapturer 的回调，按 source 分流到对应 STT + 录音"""
        await self.recorder.on_audio_frame(frame)
        if frame.source == "candidate":
            await self.candidate_stt.send_audio(frame.data)
        elif frame.source == "interviewer":
            await self.interviewer_stt.send_audio(frame.data)

    async def stop(self) -> None:
        """停止桥接（不负责关闭 STT/Recorder，由上层管理）"""
        ...
```

### 2.2 在数据流中的位置

```
AudioCapturer.set_on_frame(bridge.on_frame)
    │
    ├─→ AudioRecorder.on_audio_frame(frame)     # 写入录音
    ├─→ candidate_stt.send_audio(frame.data)    # 候选人声道 → STT 实例 1
    └─→ interviewer_stt.send_audio(frame.data)  # 面试官声道 → STT 实例 2
```

---

## 3. 转写管理器（TranscriptionManager）

STT 引擎与上层模块之间的**中间协调层**，负责接收 STT 原始结果、整理后分发给各消费者。

### 3.1 职责

- 接收 `STTEngine` 输出的 `TranscriptSegment`，按候选人/面试官分流
- 将转写文本实时推送到 WebSocket（前端展示）
- 将候选人 `is_final` 的 segment 转发给 `SuggestionTrigger`（触发建议生成）
- 将完整的轮次文本写入 `InterviewSession.rounds`（积累对话记录）
- 管理"当前轮次"的文本累积，识别轮次边界

### 3.2 轮次边界判定规则

基于说话人切换 + 沉默超时的状态机：

```
状态：INTERVIEWER_SPEAKING | CANDIDATE_SPEAKING | SILENCE

轮次边界触发条件：
  当前状态 = CANDIDATE_SPEAKING 或 SILENCE（候选人刚说完）
  且检测到 interviewer segment 到来
  → 结束当前轮次，开启新轮次
```

具体规则：
1. 面试官开始说话（收到 `source="interviewer"` 的 segment）时：
   - 如果当前轮次**已有候选人回答** → 结束当前轮次，新轮次开始
   - 如果当前轮次**没有候选人回答** → 视为面试官继续补充提问，不切轮
2. **沉默超时兜底**：60 秒无任何发言 → 强制结束当前轮次（异常情况保护）
3. 切轮时调用 `finalize_round()` 归档到 session.rounds，同时通知 `AudioRecorder.mark_round_boundary()`

### 3.3 接口定义

```python
class TranscriptionManager:
    """转写管理器 - STT 结果的分发与轮次管理"""

    def __init__(
        self,
        session: InterviewSession,
        ws_sender: Callable[[dict], Awaitable[None]],
        suggestion_trigger: SuggestionTrigger,
        recorder: AudioRecorder,
    ): ...

    async def on_segment(self, segment: TranscriptSegment) -> None:
        """接收 STT segment，分发到各消费者"""
        ...

    def get_current_round_text(self) -> tuple[str, str]:
        """返回当前轮次已累积的 (interviewer_text, candidate_text)"""
        ...

    def finalize_round(self) -> ConversationRound:
        """结束当前轮次，归档到 session.rounds，重置累积器"""
        ...
```

### 3.4 在数据流中的位置

```
STTEngine → TranscriptionManager → WebSocket（实时显示）
                                 → SuggestionTrigger（触发建议）
                                 → InterviewSession.rounds（记录对话）
                                 → AudioRecorder.mark_round_boundary()（标记轮次）
```

> 参见 [建议生成触发机制](./suggestion-trigger.md) 了解 SuggestionTrigger 的详细设计。

---

## 4. 录音管理器（AudioRecorder）

负责面试全程录音和按轮次切片。

### 4.1 接口定义

```python
class AudioRecorder:
    """录音管理器 - 完整录音 + 按轮次切片"""

    async def start_recording(self, session_id: str) -> None:
        """开始全程录音，创建两个 WAV 文件（候选人声道 + 面试官声道）"""
        ...

    async def on_audio_frame(self, frame: AudioFrame) -> None:
        """接收音频帧，写入全程录音文件"""
        ...

    def mark_round_boundary(self, round_number: int) -> None:
        """标记对话轮次边界，用于后续切片"""
        ...

    async def stop_recording(self) -> RecordingResult:
        """停止录音，生成切片文件，返回录音结果"""
        ...
```

### 4.2 存储策略

```
recordings/
└── {session_id}/
    ├── full_candidate.wav        # 候选人声道完整录音
    ├── full_interviewer.wav      # 面试官声道完整录音
    └── rounds/
        ├── round_001_candidate.wav
        ├── round_001_interviewer.wav
        ├── round_002_candidate.wav
        ├── round_002_interviewer.wav
        └── ...
```

- 完整录音：持续写入 WAV 文件，面试全程不中断
- 轮次切片：根据 `mark_round_boundary()` 标记的时间戳，在录音结束后从完整录音中切出每轮音频
- 数据库中存储文件路径引用，不存储音频二进制数据
- 面试后人工核验：前端可按轮次回放音频，对照 STT 转写文本进行修正

---

## 5. 音频管理器（AudioManager）

音频子系统的**统一生命周期管理器**，封装所有音频组件的启停协调，供 Orchestrator 调用。解决了 Orchestrator 直接管理分散音频组件的耦合问题。

### 5.1 职责

- 持有 AudioCapturer、双 STTEngine 实例、AudioRecorder 的引用
- 在面试开始时创建 AudioStreamBridge 和 TranscriptionManager，启动全部组件
- 管理 STT receive loop（后台 asyncio task），将 STT 输出桥接到 TranscriptionManager
- 在面试结束时有序停止所有组件
- 提供暂停/恢复接口（Agent 临时切换时使用）

### 5.2 接口定义

```python
class AudioManager:
    """音频子系统统一管理器"""

    def __init__(
        self,
        capturer: AudioCapturer,
        candidate_stt: STTEngine,
        interviewer_stt: STTEngine,
        recorder: AudioRecorder,
    ): ...

    async def start(
        self,
        session: InterviewSession,
        ws_sender: Callable[[dict], Awaitable[None]],
        suggestion_trigger: SuggestionTrigger,
    ) -> None:
        """启动音频采集全链路
        1. 创建 TranscriptionManager(session, ws_sender, suggestion_trigger, recorder)
        2. 创建 AudioStreamBridge(candidate_stt, interviewer_stt, recorder)
        3. 连接 AudioCapturer → Bridge (capturer.set_on_frame(bridge.on_frame))
        4. 启动 STT receive loop（后台 asyncio task × 2，消费 STT 输出 → TranscriptionManager）
        5. 连接 STT、启动采集、开始录音
        """

    async def stop(self) -> RecordingResult:
        """有序停止全部组件，返回录音结果
        顺序：音频采集 → Bridge → STT close → 取消 receive loop task
              → TranscriptionManager.finalize_round() → recorder.stop_recording()"""

    async def pause(self) -> None:
        """暂停 STT 和录音（Agent 临时切走时，不销毁连接）"""

    async def resume(self) -> None:
        """恢复 STT 和录音"""

    @property
    def transcription_manager(self) -> TranscriptionManager | None:
        """当前 TranscriptionManager 实例（start() 后可用，stop() 后为 None）"""
```

### 5.3 STT Receive Loop

AudioManager 内部通过后台 asyncio task 消费 STTEngine 的输出，桥接到 TranscriptionManager：

```python
async def _stt_receive_loop(self, stt: STTEngine) -> None:
    """后台 task：消费 STT 输出 → 转发到 TranscriptionManager"""
    async for segment in stt.receive():
        await self._transcription_manager.on_segment(segment)
```

每个 STT 实例（候选人/面试官）对应一个独立的 receive loop task（共 2 个）。task 在 `start()` 中创建，在 `stop()` 中取消。

### 5.4 Orchestrator 调用示例

```python
# 切换到面试 Agent 时
async def _switch_to_interview(self):
    await self._interview_agent.on_activate(self._session)
    await self._audio_manager.start(
        session=self._session,
        ws_sender=self._ws_sender,
        suggestion_trigger=self._interview_agent.suggestion_trigger,
    )

# 结束面试时
async def _switch_from_interview(self):
    await self._interview_agent.on_deactivate(self._session)
    recording_result = await self._audio_manager.stop()
    # recording_result 用于更新 session 中的录音路径
```

### 5.5 在数据流中的位置

```
Orchestrator
  └─ AudioManager.start() / stop() / pause() / resume()
       │
       ├─ AudioCapturer → Bridge.on_frame()
       │                     ├─→ candidate_stt.send_audio()
       │                     ├─→ interviewer_stt.send_audio()
       │                     └─→ recorder.on_audio_frame()
       │
       └─ STT receive loop (×2)
              └─→ TranscriptionManager.on_segment()
                     ├─→ WebSocket 推送转写
                     ├─→ SuggestionTrigger.on_candidate_segment()
                     └─→ session.rounds 记录
```

> 数据类型定义见 [共享数据结构](./data-models.md)

---

## 6. 设计决策

### 决策 8: 录音存储

```
├── 方案 A: 仅存完整录音
├── 方案 B: 完整录音 + 按轮次切片
└── 选择: 方案 B
    理由: 完整录音用于全程回放，轮次切片方便定位特定回答进行 STT 核验。
         音频文件存储在本地文件系统，数据库中仅存路径引用。
```

### 决策 11: 是否引入 webrtcvad（帧级语音活动检测）

```
├── 方案 A: 引入 webrtcvad，在发送音频帧前过滤静音帧
├── 方案 B: 不引入 webrtcvad，依赖百度 STT 自带的 VAD 机制
└── 选择: 方案 B（MVP 阶段）
    理由:
    1. 百度实时语音识别 API 内置 VAD，会在检测到句子结束时输出 is_final=True 的
       segment。SuggestionTrigger 和 TranscriptionManager 均基于此信号工作，
       不需要原始帧级 VAD。
    2. webrtcvad 对音频帧大小有严格要求（必须是 160/320/480 样本，即
       10/20/30ms@16kHz），需要额外的帧对齐缓冲逻辑，增加实现复杂度。
    3. 双 STT 实例（候选人/面试官各一）已通过 source 字段区分说话人，
       无需 VAD 辅助轮次边界判断。
    4. 百度 STT 按时长计费，发送少量静音帧影响可忽略不计。
    后续优化: 若发现带宽或 STT 延迟有问题，可在 AudioStreamBridge.on_frame()
             中加入静音过滤逻辑，此时再引入 webrtcvad，不影响其他模块。
```

> 注：项目中不包含 `vad.py` 模块，`webrtcvad` 不列入依赖。
